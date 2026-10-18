#!/usr/bin/env python3
"""Patch an owned Darling Registry header; never modify reference sources."""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('resource registry anchor is not unique: ' + old[:80])
    return text.replace(old, new)


def patch_registry(text):
    text = once(text, '#include <functional>', '#include <functional>\n#include <stdexcept>')
    text = once(text, 'uint64_t _eternalCounter;', 'uint64_t _eternalCounter = 0;')
    text = once(text, 'static thread_local bool _registeringWithLockHeld;', '''// The bypass belongs to this registry instance, only during its factory.
		class FactoryScope;
		static thread_local FactoryScope* _registeringWithLockHeld;

		class FactoryScope {
			friend class Registry;
			const Registry* _registry;
			FactoryScope* _previous;
		public:
			explicit FactoryScope(const Registry* registry): _registry(registry), _previous(_registeringWithLockHeld) {
				_registeringWithLockHeld = this;
			}
			~FactoryScope() { _registeringWithLockHeld = _previous; }
			FactoryScope(const FactoryScope&) = delete;
			FactoryScope& operator=(const FactoryScope&) = delete;
		};

		bool factoryOwnsLock() const {
			for (auto scope = _registeringWithLockHeld; scope; scope = scope->_previous)
				if (scope->_registry == this) return true;
			return false;
		}

		// ID/NSID/EternalID are integer keys in both actual consumers. All
		// allocations precede publication. Historical eternal-ID entries
		// intentionally survive replacement
		// (kernel threads share their numeric host ID).
		void publishEntry(const std::shared_ptr<Entry>& entry) {
			if (_eternalCounter == EternalIDInvalid)
				throw std::overflow_error("Registry eternal ID exhausted");
			const auto id = entry->id();
			const auto nsid = entry->nsid();
			const auto eid = _eternalCounter;
			bool addedID = false, addedNSID = false, addedEID = false;
			try {
				addedID = _map.try_emplace(id, entry).second;
				addedNSID = _nsmap.try_emplace(nsid, entry).second;
				addedEID = _emap.try_emplace(eid, entry).second;
				if (!addedEID) throw std::logic_error("Registry eternal ID collision");
				// Actual Process/Thread setters only assign the integer field.
				entry->_setEternalID(eid);
			} catch (...) {
				if (addedEID) _emap.erase(eid);
				if (addedNSID) _nsmap.erase(nsid);
				if (addedID) _map.erase(id);
				throw;
			}
			// Integer-key lookup and shared_ptr assignment cannot allocate.
			_map.find(id)->second = entry;
			_nsmap.find(nsid)->second = entry;
			++_eternalCounter;
		}''')
    text = once(text, '''			_registeringWithLockHeld = true;
			auto entry = entryFactory();
			_registeringWithLockHeld = false;''', '''			auto entry = [&]() {
				FactoryScope scope(this);
				return entryFactory();
			}();''')
    block = '''			entry->_setEternalID(_eternalCounter++);

			_map[entry->id()] = entry;
			_nsmap[entry->nsid()] = entry;
			_emap[entry->eternalID()] = entry;'''
    if text.count(block) != 2:
        raise ValueError('expected both registry publication sites')
    text = text.replace(block, '\t\t\tpublishEntry(entry);')
    if text.count('if (!_registeringWithLockHeld)') != 3:
        raise ValueError('expected three registry lookup sites')
    text = text.replace('if (!_registeringWithLockHeld)', 'if (!factoryOwnsLock())')
    return once(text, 'thread_local bool DarlingServer::Registry<T>::_registeringWithLockHeld = false;',
                'thread_local typename DarlingServer::Registry<T>::FactoryScope* DarlingServer::Registry<T>::_registeringWithLockHeld = nullptr;')
