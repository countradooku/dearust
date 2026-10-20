#!/usr/bin/env python3
"""Reserve Checkin reply storage before execution, preserving the common FIFO.

An embedded node follows Message moves through interrupted/S2C reply deferral.
Its ordinary-message gap merges it into the existing queue without another
allocation, a sequence counter, or a separate reply transport.
"""
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared reply anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


NODE = '''
    struct PreparedReplyNode {
        std::optional<Message> storage;
        std::shared_ptr<void> owner;
        PreparedReplyNode* next = nullptr;
        size_t ordinaryBefore = 0;
        bool queued = false;
        explicit PreparedReplyNode(size_t size): storage(std::in_place, size, 0) {}
        ~PreparedReplyNode() {
            if (queued) std::terminate();
            if (storage) storage->_preparedReply = nullptr;
        }
        Message take(std::shared_ptr<void> value) {
            if (queued || owner || !storage) std::terminate();
            owner = std::move(value);
            Message message(std::move(*storage));
            storage.reset();
            message._preparedReply = this;
            return message;
        }
    };
'''


def patch_prepared_replies(files):
    files = dict(files)
    header = files['message.hpp']
    header = once(header, '#include <array>', '#include <array>\n#include <memory>\n#include <utility>')
    header = once(header, '\tclass Message {', '    struct PreparedReplyNode;\n\tclass Message {\n        friend struct PreparedReplyNode;\n        friend class MessageQueue;')
    header = once(header, '\t\tstruct msghdr _header;', '        PreparedReplyNode* _preparedReply = nullptr;\n\t\tstruct msghdr _header;')
    header = once(header, '\tclass MessageQueue {', NODE + '\n\tclass MessageQueue {')
    header = once(header, '\t\tstd::deque<Message> _messages;', '''
        PreparedReplyNode* _preparedHead = nullptr;
        PreparedReplyNode* _preparedTail = nullptr;
        size_t _ordinaryTail = 0;
        void _pushLocked(Message&& message);
        std::optional<Message> _popLocked();
        bool _emptyLocked() const { return _messages.empty() && !_preparedHead; }
\t\tstd::deque<Message> _messages;''')
    header = once(header, '\t\tvoid push(Message&& message);', '\t\t~MessageQueue();\n\t\tvoid push(Message&& message);')
    header = once(header, '\t\tstd::function<void()> _messageArrivalNotificationCallback = nullptr;',
                  '\t\tstd::shared_ptr<const std::function<void()>> _messageArrivalNotificationCallback;')
    files['message.hpp'] = header
    cpp = files['message.cpp']
    cpp = once(cpp, 'void DarlingServer::Message::_initWithOther(Message&& other) {', '''void DarlingServer::Message::_initWithOther(Message&& other) {
    _preparedReply = std::exchange(other._preparedReply, nullptr);''')
    start = cpp.index('void DarlingServer::Message::_cleanupSelf() {')
    end = cpp.index('\n};', start)
    cpp = cpp[:end] + '''
    // Clear the token first: dropping this owner may destroy its embedded node.
    if (auto* node = std::exchange(_preparedReply, nullptr)) {
        auto owner = std::move(node->owner);
    }
''' + cpp[end:]
    start = cpp.index('void DarlingServer::MessageQueue::push(Message&& message) {')
    end = cpp.index('// darling-lol M58ak:', start)
    cpp = cpp[:start] + (Path(__file__).with_name('prepared-resource-reply-queue.inc').read_text()) + '\n' + cpp[end:]
    start = cpp.index('bool DarlingServer::MessageQueue::sendMany(int socket) {')
    end = cpp.index('bool DarlingServer::MessageQueue::receiveMany(int socket) {', start)
    cpp = cpp[:start] + (Path(__file__).with_name('prepared-resource-reply-send.inc').read_text()) + '\n' + cpp[end:]
    # Inbox storage is still ordinary, but uses the same gap bookkeeping.
    cpp = once(cpp, '\t\t\t_messages.push_back(std::move(message));', '\t\t\t_pushLocked(std::move(message));')
    cpp = once(cpp, '\treturn _messages.empty();', '\treturn _emptyLocked();')
    cpp = once(cpp, '\t\tif (_messages.empty()) {', '\t\tif (_emptyLocked()) {')
    cpp = once(cpp, '\t\t_messages.push_back(std::move(message));', '\t\t_pushLocked(std::move(message));')
    # Install the callback owner at setup. Taking a shared owner cannot allocate,
    # and invoking outside the mutex preserves callback reentrancy/replacement.
    begin = cpp.index('void DarlingServer::MessageQueue::setMessageArrivalNotificationCallback(')
    end = cpp.index('\nbool DarlingServer::MessageQueue::empty()', begin)
    cpp = cpp[:begin] + '''void DarlingServer::MessageQueue::setMessageArrivalNotificationCallback(std::function<void()> callback) {
    auto replacement = std::make_shared<const std::function<void()>>(std::move(callback));
    {
        std::unique_lock lock(_lock);
        _messageArrivalNotificationCallback.swap(replacement);
    }
    // A capture's final destructor may reenter this queue.
}
''' + cpp[end:]
    cpp = once(cpp, '\t\t\tcallback();', '\t\t\t(*callback)();')
    cpp = once(cpp, '\t\tif (callback) {', '\t\tif (callback && *callback) {')
    begin = cpp.index('void DarlingServer::MessageQueue::_notifyMessageArrival() {')
    end = cpp.index('void DarlingServer::MessageQueue::pushAndSend', begin)
    cpp = cpp[:begin] + '''void DarlingServer::MessageQueue::_notifyMessageArrival() {
    std::shared_ptr<const std::function<void()>> callback;
    {
        std::scoped_lock lock(_lock);
        if (_emptyLocked()) return;
        callback = _messageArrivalNotificationCallback;
    }
    if (callback && *callback) (*callback)();
}

''' + cpp[end:]
    files['message.cpp'] = cpp
    rpc = files['rpc.internal.h']
    begin = rpc.index('\tclass Call::Checkin:')
    end = rpc.index('\tclass Call::Checkout:', begin)
    checkin = rpc[begin:end]
    checkin = once(checkin, '\t\tdserver_call_checkin_t _body; \\\n', '\t\tdserver_call_checkin_t _body; \\\n        PreparedReplyNode _preparedReply{sizeof(dserver_rpc_reply_checkin_t)}; \\\n')
    begin_log = checkin.index('\t\t\trpcReplyLog.debug()')
    end_log = checkin.index('\n', begin_log)
    checkin = checkin[:begin_log] + checkin[end_log+1:]
    checkin = once(checkin, 'Message reply(sizeof(dserver_rpc_reply_checkin_t), 0);', 'Message reply = _preparedReply.take(shared_from_this());')
    checkin = once(checkin, 'Message&& requestMessage):', 'Message&& requestMessage, bool deferLifetimePipe = false):')
    checkin = once(checkin, '_body.lifetime_listener_pipe = requestMessage.extractDescriptorAtIndex(_body.lifetime_listener_pipe);',
        '_body.lifetime_listener_pipe = deferLifetimePipe ? -1 : requestMessage.extractDescriptorAtIndex(_body.lifetime_listener_pipe);')
    checkin = once(checkin, 'void _sendReply(int resultCode) {', 'void _sendReply(int resultCode, bool terminal = false) {')
    checkin = once(checkin, 'thread->pushCallReply(shared_from_this(), std::move(reply));',
        'if (terminal) { thread->deactivateCall(shared_from_this()); Call::sendReply(std::move(reply)); } else thread->pushCallReply(shared_from_this(), std::move(reply));')
    rpc = rpc[:begin] + checkin + rpc[end:]
    files['rpc.internal.h'] = rpc
    call = files['call.cpp']
    call = once(call, '    try {\n\tif ((header->number & DSERVER_CALL_UNMANAGED_FLAG) == 0) {', '''    std::shared_ptr<Checkin> preparedCheckin;
    try {
        if (preparedIsCheckin) {
            if (requestMessage.data().size() < sizeof(dserver_rpc_call_checkin_t))
                throw std::invalid_argument("Message buffer was too small for checkin");
            // Reserve both Call ownership and all reply storage before either
            // Registry can publish C handles or monitor side effects.
            preparedCheckin = std::make_shared<Checkin>(nullptr,
                reinterpret_cast<dserver_rpc_call_checkin_t*>(header), std::move(requestMessage), true);
        }
\tif ((header->number & DSERVER_CALL_UNMANAGED_FLAG) == 0) {''')
    call = once(call, '\tif (callLog.levelEnabled(1)) {', '\tif (!preparedIsCheckin && callLog.levelEnabled(1)) {')
    begin = call.index('\tswitch (header->number) {\n\t\tDSERVER_CONSTRUCT_CASES')
    end = call.index('\n\t#undef CALL_CASE', begin)
    call = call[:begin] + '''    try {
    if (preparedCheckin) {
        preparedCheckin->_thread = thread;
        const auto* body = reinterpret_cast<dserver_rpc_call_checkin_t*>(header);
        preparedCheckin->_body.lifetime_listener_pipe = requestMessage.extractDescriptorAtIndex(body->body.lifetime_listener_pipe);
        result = std::move(preparedCheckin);
    } else {
''' + call[begin:end] + '''
    }
    } catch (const std::bad_alloc&) {
        if (!preparedIsCheckin) throw;
        Server::sharedInstance().queuePreparedCheckinFailure(preparedCheckinAddress, ENOMEM);
        return nullptr;
    }
''' + call[end:]
    begin = call.index('void DarlingServer::Call::Checkin::processCall() {')
    end = call.index('void DarlingServer::Call::Checkout::processCall()', begin)
    call = call[:begin] + '''void DarlingServer::Call::Checkin::processCall() {
    int code = -ESRCH;
    std::shared_ptr<Process> failedProcess;
    if (auto thread = _thread.lock()) {
        if (auto process = thread->process()) {
            try {
                process->notifyCheckin(static_cast<Process::Architecture>(_header.architecture));
                code = 0;
            } catch (const std::bad_alloc&) {
                code = -ENOMEM; failedProcess = process;
            } catch (const std::system_error& error) {
                const auto& category = error.code().category();
                code = -((&category == &std::generic_category() || &category == &std::system_category())
                    ? error.code().value() : EIO);
                failedProcess = process;
            } catch (const std::runtime_error&) {
                code = -EIO; failedProcess = process;
            }
        }
    }
    // mldr exits on failure; fork checkin aborts. A physical exec has already
    // happened, so old-image interrupt/S2C state cannot defer this terminal
    // error. Queue its reserved datagram before retiring the logical Process.
    _sendReply(code, bool(failedProcess));
    if (failedProcess) failedProcess->notifyDead();
};

''' + call[end:]
    files['call.cpp'] = call
    if 'server.cpp' in files:
        files['server.cpp'] = once(files['server.cpp'], '\t\t\t\t\t_workQueue.push(call->thread());',
            '\t\t\t\t\tif (call->number() == Call::Number::Checkin) scheduleResumedThread(call->thread());\n'
            '\t\t\t\t\telse _workQueue.push(call->thread());')
    return files
