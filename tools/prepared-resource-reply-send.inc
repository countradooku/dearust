bool DarlingServer::MessageQueue::sendMany(int socket) {
    std::unique_lock lock(_lock);
    while (!_emptyLocked()) {
        struct mmsghdr batch[16] = {};
        size_t count = 0;
        size_t ordinary = 0;
        auto* node = _preparedHead;
        size_t gap = node ? node->ordinaryBefore : _ordinaryTail;
        while (count < 16) {
            if (node && !gap) {
                batch[count++].msg_hdr = node->storage->rawHeader();
                node = node->next;
                gap = node ? node->ordinaryBefore : _ordinaryTail;
            } else if (ordinary < _messages.size()) {
                batch[count++].msg_hdr = _messages[ordinary++].rawHeader();
                --gap;
            } else break;
        }
        for (size_t i = 0; i < count; ++i) {
            auto* address = reinterpret_cast<sockaddr_un*>(batch[i].msg_hdr.msg_name);
            bool empty = true;
            for (size_t j = 0; j < sizeof(address->sun_path); ++j)
                if (address->sun_path[j]) { empty = false; break; }
            if (empty) { batch[i].msg_hdr.msg_name = nullptr; batch[i].msg_hdr.msg_namelen = 0; }
        }
        int sent = sendmmsg(socket, batch, count, MSG_DONTWAIT | MSG_NOSIGNAL);
        bool dropped = false;
        if (sent < 0) {
            const int error = errno;
            if (error == EAGAIN || error == EWOULDBLOCK) return false;
            if (error == EINTR) continue;
            if (error == EPIPE || error == ECONNREFUSED || error == ENOENT) {
                sent = 1; dropped = true;
            } else throw std::system_error(error, std::generic_category(), "Failed to send messages through socket");
        }
        std::optional<Message> retired[16];
        for (int i = 0; i < sent; ++i) retired[i] = _popLocked();
        lock.unlock();
        for (int i = 0; i < sent; ++i) {
            if (!dropped) {
                m58ak_log_message("send", socket, &retired[i]->address().raw(), retired[i]->data().data(), retired[i]->data().size());
                // Calling in place avoids an allocating std::function copy.
                if (retired[i]->_sendNotificationCallback) retired[i]->_sendNotificationCallback();
            }
            retired[i].reset();
        }
        lock.lock();
    }
    return true;
}
