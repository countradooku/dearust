void DarlingServer::MessageQueue::_pushLocked(Message&& message) {
    if (auto* node = message._preparedReply) {
        if (node->queued || node->storage || !node->owner) std::terminate();
        message._preparedReply = nullptr;
        node->storage.emplace(std::move(message));
        node->ordinaryBefore = _ordinaryTail;
        _ordinaryTail = 0;
        node->next = nullptr;
        node->queued = true;
        if (_preparedTail) _preparedTail->next = node;
        else _preparedHead = node;
        _preparedTail = node;
    } else {
        _messages.push_back(std::move(message));
        ++_ordinaryTail; // bounded by the successfully allocated deque size
    }
}

std::optional<DarlingServer::Message> DarlingServer::MessageQueue::_popLocked() {
    if (_preparedHead && !_preparedHead->ordinaryBefore) {
        auto* node = _preparedHead;
        _preparedHead = node->next;
        if (!_preparedHead) _preparedTail = nullptr;
        node->queued = false;
        node->next = nullptr;
        Message message(std::move(*node->storage));
        node->storage.reset();
        message._preparedReply = node;
        return message;
    }
    if (_messages.empty()) return std::nullopt;
    Message message(std::move(_messages.front()));
    _messages.pop_front();
    if (_preparedHead) --_preparedHead->ordinaryBefore;
    else --_ordinaryTail;
    return message;
}

DarlingServer::MessageQueue::~MessageQueue() {
    // Call/Thread destruction may itself send messages. Release all retained
    // owners outside the queue mutex, just as normal transport retirement does.
    while (auto message = pop()) {}
}

void DarlingServer::MessageQueue::push(Message&& message) {
    std::unique_lock lock(_lock);
    _pushLocked(std::move(message));
    auto callback = _messageArrivalNotificationCallback;
    lock.unlock();
    if (callback && *callback) (*callback)();
}

std::optional<DarlingServer::Message> DarlingServer::MessageQueue::pop() {
    std::unique_lock lock(_lock);
    return _popLocked();
}
