// Included only by the owned copy of Darling's call.cpp. Received descriptors
// belong to the generated Call class; the Rust backend borrows them.
#include <array>
#include <fstream>
#include <sstream>
#include <sys/stat.h>

extern "C" long dearust_filesystem_host_snapshot(int, int, const unsigned char*, unsigned char*, unsigned char*, const unsigned char*);

namespace {
bool filesystemReadProc(const std::string& path, std::string& output) {
    std::ifstream input(path, std::ios::binary);
    if (!input) return false;
    std::array<char, 4096> chunk;
    output.clear();
    while (input.read(chunk.data(), chunk.size()) || input.gcount()) {
        if (output.size() + static_cast<size_t>(input.gcount()) > 2 * 1024 * 1024) return false;
        output.append(chunk.data(), static_cast<size_t>(input.gcount()));
    }
    return input.eof();
}

bool filesystemSameObject(const std::string& first, const std::string& second) {
    struct stat a = {}, b = {};
    return stat(first.c_str(), &a) == 0 && stat(second.c_str(), &b) == 0
        && a.st_dev == b.st_dev && a.st_ino == b.st_ino;
}

bool filesystemPeerPolicy(const std::string& peer, bool resolvingPath) {
    // A received descriptor still invokes statfs authorization under the
    // server thread's credentials/LSM context. Namespace-specific numeric IDs
    // cannot establish identity without also comparing the user namespace.
    if (!filesystemSameObject("/proc/thread-self/ns/user", peer + "/ns/user")) return false;
    // Descriptor capabilities retain their client mount even when it is absent
    // from the server namespace. Pathname resolution additionally requires the
    // same mount namespace and Linux root.
    if (resolvingPath
        && (!filesystemSameObject("/proc/thread-self/ns/mnt", peer + "/ns/mnt")
            || !filesystemSameObject("/proc/thread-self/root", peer + "/root"))) return false;
    std::string local, remote;
    if (!filesystemReadProc("/proc/thread-self/status", local)
        || !filesystemReadProc(peer + "/status", remote)) return false;
    for (const auto* key : {"Uid:", "Gid:", "Groups:", "CapEff:"}) {
        const auto line = [key](const std::string& text) {
            std::istringstream lines(text);
            std::string result;
            while (std::getline(lines, result)) {
                if (result.compare(0, strlen(key), key) == 0) return result;
            }
            return std::string();
        };
        const auto a = line(local), b = line(remote);
        if (a.empty() || a != b) return false;
    }
    // LSM labels can impose different access despite matching DAC credentials.
    if (!filesystemReadProc("/proc/thread-self/attr/current", local)
        || !filesystemReadProc(peer + "/attr/current", remote)) return false;
    return local == remote;
}

int32_t filesystemCopy(pid_t pid, bool writing, uint64_t address, void* local, size_t length) {
    struct iovec here = {local, length};
    struct iovec there = {reinterpret_cast<void*>(static_cast<uintptr_t>(address)), length};
    const auto count = writing ? process_vm_writev(pid, &here, 1, &there, 1, 0)
                               : process_vm_readv(pid, &here, 1, &there, 1, 0);
    // The existing Process helpers accept short positive copies. This boundary
    // must report a fault rather than expose an incomplete initialized result.
    if (count == static_cast<ssize_t>(length)) return 0;
    if (count >= 0) return -14;
    // The documented process_vm errors have these Darwin values. Preserve
    // permission and process-lifetime failures instead of disguising a fault.
    switch (errno) {
        case EPERM: return -1;
        case ESRCH: return -3;
        case EINTR: return -4;
        case ENOMEM: return -12;
        case EFAULT: return -14;
        case EINVAL: return -22;
        case ENOSYS: return -78;
        default: return -5;
    }
}
}

void DarlingServer::Call::FilesystemSnapshot::processCall() {
    int32_t status = -3; // Darwin ESRCH
    try {
        if (auto thread = _thread.lock()) {
            if (auto process = thread->process()) {
                std::array<unsigned char, 4096> path = {};
                std::array<unsigned char, 4096> point = {};
                std::array<unsigned char, 2168> output = {};
                status = -22; // Darwin EINVAL
                const bool descriptor = _body.descriptor >= 0;
                const bool valid = descriptor
                    ? (_body.directory == -1 && _body.path == 0 && _body.path_length == 0)
                    : (_body.path != 0 && _body.path_length > 0 && _body.path_length <= path.size());
                if (valid) {
                    status = 0;
                    if (!descriptor) {
                        status = filesystemCopy(process->id(), false, _body.path, path.data(), _body.path_length);
                        if (status == 0 && (path[_body.path_length - 1] != 0
                            || memchr(path.data(), 0, _body.path_length - 1)
                            || (path[0] != '/' && _body.directory < 0))) {
                            status = -22;
                        }
                    }
                    const auto peer = "/proc/" + std::to_string(process->id())
                        + "/task/" + std::to_string(thread->id());
                    // The client performs its own statfs authorization probe;
                    // compare identities as well before delegating that work.
                    if (status == 0 && !filesystemPeerPolicy(peer, !descriptor)) status = -1;
                    if (status == 0) {
                        const auto mountinfo = peer + "/mountinfo";
                        const long result = dearust_filesystem_host_snapshot(_body.descriptor,
                            _body.directory, descriptor ? nullptr : path.data(), output.data(), point.data(),
                            reinterpret_cast<const unsigned char*>(mountinfo.c_str()));
                        status = result < INT32_MIN || result > 0 ? -5 : static_cast<int32_t>(result);
                    }
                    if (status == 0) status = filesystemCopy(process->id(), true, _body.output, output.data(), output.size());
                    if (status == 0) status = filesystemCopy(process->id(), true, _body.mountpoint, point.data(), point.size());
                }
            }
        }
    } catch (const std::bad_alloc&) {
        status = -12;
    } catch (...) {
        status = -5;
    }
    _sendReply(0, status);
}
