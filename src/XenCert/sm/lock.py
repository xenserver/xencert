#
# Copyright (C) Citrix Systems Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; version 2.1 only.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

"""Serialization for concurrent operations"""

import os
import errno
import util
import fcntl
import struct
import lvutil

VERBOSE = True
NS_PREFIX_LVM = "lvm-"


class RefCounterException(util.SMException):
    pass


class RefCounter:
    """Persistent local-FS file-based reference counter. The
    operations are get() and put(), and they are atomic."""

    BASE_DIR = "/var/run/sm/refcount"

    def get(obj, binary, ns=None):
        """Get (inc ref count) 'obj' in namespace 'ns' (optional).
        Returns new ref count"""
        if binary:
            return RefCounter._adjust(ns, obj, 0, 1)
        else:
            return RefCounter._adjust(ns, obj, 1, 0)
    get = staticmethod(get)

    def put(obj, binary, ns=None):
        """Put (dec ref count) 'obj' in namespace 'ns' (optional). If ref
        count was zero already, this operation is a no-op.
        Returns new ref count"""
        if binary:
            return RefCounter._adjust(ns, obj, 0, -1)
        else:
            return RefCounter._adjust(ns, obj, -1, 0)
    put = staticmethod(put)

    def set(obj, count, binaryCount, ns=None):
        """Set normal & binary counts explicitly to the specified values.
        Returns new ref count"""
        (obj, ns) = RefCounter._getSafeNames(obj, ns)
        assert(count >= 0 and binaryCount >= 0)
        if binaryCount > 1:
            raise RefCounterException("Binary count = %d > 1" % binaryCount)
        RefCounter._set(ns, obj, count, binaryCount)
    set = staticmethod(set)

    def check(obj, ns=None):
        """Get the ref count values for 'obj' in namespace 'ns' (optional)"""
        (obj, ns) = RefCounter._getSafeNames(obj, ns)
        return RefCounter._get(ns, obj)
    check = staticmethod(check)



    def reset(obj, ns=None):
        """Reset ref counts for 'obj' in namespace 'ns' (optional) to 0."""
        RefCounter.resetAll(ns, obj)
    reset = staticmethod(reset)

    def resetAll(ns=None, obj=None):
        """Reset ref counts of 'obj' in namespace 'ns' to 0. If obj is not
        provided, reset all existing objects in 'ns' to 0. If neither obj nor
        ns are supplied, do this for all namespaces"""
        if obj:
            (obj, ns) = RefCounter._getSafeNames(obj, ns)
        if ns:
            nsList = [ns]
        else:
            if not util.pathexists(RefCounter.BASE_DIR):
                return
            try:
                nsList = os.listdir(RefCounter.BASE_DIR)
            except OSError:
                raise RefCounterException("failed to get namespace list")
        for ns in nsList:
            RefCounter._reset(ns, obj)
    resetAll = staticmethod(resetAll)

    def _adjust(ns, obj, delta, binaryDelta):
        """Add 'delta' to the normal refcount and 'binaryDelta' to the binary
        refcount of 'obj' in namespace 'ns'.
        Returns new ref count"""
        if binaryDelta > 1 or binaryDelta < -1:
            raise RefCounterException("Binary delta = %d outside [-1;1]" % \
                    binaryDelta)
        (obj, ns) = RefCounter._getSafeNames(obj, ns)
        (count, binaryCount) = RefCounter._get(ns, obj)

        newCount = count + delta
        newBinaryCount = binaryCount + binaryDelta
        if newCount < 0:
            util.SMlog("WARNING: decrementing normal refcount of 0")
            newCount = 0
        if newBinaryCount < 0:
            util.SMlog("WARNING: decrementing binary refcount of 0")
            newBinaryCount = 0
        if newBinaryCount > 1:
            newBinaryCount = 1
        util.SMlog("Refcount for %s:%s (%d, %d) + (%d, %d) => (%d, %d)" % \
                (ns, obj, count, binaryCount, delta, binaryDelta,
                    newCount, newBinaryCount))
        RefCounter._set(ns, obj, newCount, newBinaryCount)
        return newCount + newBinaryCount
    _adjust = staticmethod(_adjust)

    def _get(ns, obj):
        """Get the ref count values for 'obj' in namespace 'ns'"""
        objFile = os.path.join(RefCounter.BASE_DIR, ns, obj)
        (count, binaryCount) = (0, 0)
        if util.pathexists(objFile):
            (count, binaryCount) = RefCounter._readCount(objFile)
        return (count, binaryCount)
    _get = staticmethod(_get)

    def _set(ns, obj, count, binaryCount):
        """Set the ref count values for 'obj' in namespace 'ns'"""
        util.SMlog("Refcount for %s:%s set => (%d, %db)" % \
                (ns, obj, count, binaryCount))
        if count == 0 and binaryCount == 0:
            RefCounter._removeObject(ns, obj)
        else:
            objFile = os.path.join(RefCounter.BASE_DIR, ns, obj)

            while not RefCounter._writeCount(objFile, count, binaryCount):
                RefCounter._createNamespace(ns)

    _set = staticmethod(_set)

    def _getSafeNames(obj, ns):
        """Get a name that can be used as a file name"""
        if not ns:
            ns = obj.split('/')[0]
            if not ns:
                ns = "default"
        for char in ['/', '*', '?', '\\']:
            obj = obj.replace(char, "_")
        return (obj, ns)
    _getSafeNames = staticmethod(_getSafeNames)

    def _createNamespace(ns):
        nsDir = os.path.join(RefCounter.BASE_DIR, ns)
        try:
            os.makedirs(nsDir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise RefCounterException("failed to makedirs '%s' (%s)" % \
                        (nsDir, e))
    _createNamespace = staticmethod(_createNamespace)

    def _removeObject(ns, obj):
        nsDir = os.path.join(RefCounter.BASE_DIR, ns)
        objFile = os.path.join(nsDir, obj)
        if not util.pathexists(objFile):
            return
        try:
            os.unlink(objFile)
        except OSError:
            raise RefCounterException("failed to remove '%s'" % objFile)

        try:
            os.rmdir(nsDir)
        except OSError as e:
            namespaceAlreadyCleanedUp = e.errno == errno.ENOENT
            newObjectAddedToNamespace = e.errno == errno.ENOTEMPTY

            if namespaceAlreadyCleanedUp or newObjectAddedToNamespace:
                pass
            else:
                raise RefCounterException("failed to remove '%s'" % nsDir)
    _removeObject = staticmethod(_removeObject)

    def _reset(ns, obj=None):
        nsDir = os.path.join(RefCounter.BASE_DIR, ns)
        if not util.pathexists(nsDir):
            return
        if obj:
            if not util.pathexists(os.path.join(nsDir, obj)):
                return
            objList = [obj]
        else:
            try:
                objList = os.listdir(nsDir)
            except OSError:
                raise RefCounterException("failed to list '%s'" % ns)
        for obj in objList:
            RefCounter._removeObject(ns, obj)
    _reset = staticmethod(_reset)

    def _readCount(fn):
        try:
            f = open(fn, 'r')
            line = f.readline()
            nums = line.split()
            count = int(nums[0])
            binaryCount = int(nums[1])
            f.close()
        except IOError:
            raise RefCounterException("failed to read file '%s'" % fn)
        return (count, binaryCount)
    _readCount = staticmethod(_readCount)

    def _writeCount(fn, count, binaryCount):
        try:
            f = open(fn, 'w')
            f.write("%d %d\n" % (count, binaryCount))
            f.close()
            return True
        except IOError as e:
            fileNotFound = e.errno == errno.ENOENT
            if fileNotFound:
                return False
            raise RefCounterException("failed to write '(%d %d)' to '%s': %s" \
                    % (count, binaryCount, fn, e))
    _writeCount = staticmethod(_writeCount)

    def _runTests():
        "Unit tests"

        RefCounter.resetAll()

        # A
        (cnt, bcnt) = RefCounter.check("X", "A")
        if cnt != 0 or bcnt != 0:
            print("Error: check = %d != 0 in the beginning" % cnt)
            return -1

        cnt = RefCounter.get("X", False, "A")
        if cnt != 1:
            print("Error: count = %d != 1 after first get()" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("X", "A")
        if cnt != 1:
            print("Error: check = %d != 1 after first get()" % cnt)
            return -1

        cnt = RefCounter.put("X", False, "A")
        if cnt != 0:
            print("Error: count = %d != 0 after get-put" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("X", "A")
        if cnt != 0:
            print("Error: check = %d != 0 after get-put" % cnt)
            return -1

        cnt = RefCounter.get("X", False, "A")
        if cnt != 1:
            print("Error: count = %d != 1 after get-put-get" % cnt)
            return -1

        cnt = RefCounter.get("X", False, "A")
        if cnt != 2:
            print("Error: count = %d != 2 after second get()" % cnt)
            return -1

        cnt = RefCounter.get("X", False, "A")
        if cnt != 3:
            print("Error: count = %d != 3 after third get()" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("X", "A")
        if cnt != 3:
            print("Error: check = %d != 3 after third get()" % cnt)
            return -1

        cnt = RefCounter.put("Y", False, "A")
        if cnt != 0:
            print("Error: count = %d != 0 after first put()" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("Y", "A")
        if cnt != 0:
            print("Error: check = %d != 0 after first put()" % cnt)
            return -1

        cnt = RefCounter.put("X", False, "A")
        if cnt != 2:
            print("Error: count = %d != 2 after 3get-1put" % cnt)
            return -1

        cnt = RefCounter.put("X", False, "A")
        if cnt != 1:
            print("Error: count = %d != 1 after 3get-2put" % cnt)
            return -1

        cnt = RefCounter.get("X", False, "A")
        if cnt != 2:
            print("Error: count = %d != 2 after 4get-2put" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("X", "A")
        if cnt != 2:
            print("Error: check = %d != 2 after 4get-2put" % cnt)
            return -1

        cnt = RefCounter.put("X", False, "A")
        if cnt != 1:
            print("Error: count = %d != 0 after 4get-3put" % cnt)
            return -1

        cnt = RefCounter.put("X", False, "A")
        if cnt != 0:
            print("Error: count = %d != 0 after 4get-4put" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("X", "A")
        if cnt != 0:
            print("Error: check = %d != 0 after 4get-4put" % cnt)
            return -1

        # B
        cnt = RefCounter.put("Z", False, "B")
        if cnt != 0:
            print("Error: count = %d != 0 after new put()" % cnt)
            return -1

        cnt = RefCounter.get("Z", False, "B")
        if cnt != 1:
            print("Error: count = %d != 1 after put-get" % cnt)
            return -1

        cnt = RefCounter.put("Z", False, "B")
        if cnt != 0:
            print("Error: count = %d != 0 after put-get-put" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("Z", "B")
        if cnt != 0:
            print("Error: check = %d != 0 after put-get-put" % cnt)
            return -1

        cnt = RefCounter.get("Z", False, "B")
        if cnt != 1:
            print("Error: count = %d != 1 after put-get-put-get" % cnt)
            return -1
        (cnt, bcnt) = RefCounter.check("Z", "B")
        if cnt != 1:
            print("Error: check = %d != 1 after put-get-put-get" % cnt)
            return -1

        # set
        (cnt, bcnt) = RefCounter.check("a/b")
        if cnt != 0:
            print("Error: count = %d != 0 initially" % cnt)
            return -1
        RefCounter.set("a/b", 2, 0)
        (cnt, bcnt) = RefCounter.check("a/b")
        if cnt != 2 or bcnt != 0:
            print("Error: count = (%d,%d) != (2,0) after set(2,0)" % (cnt, bcnt))
            return -1
        cnt = RefCounter.put("a/b", False)
        if cnt != 1:
            print("Error: count = %d != 1 after set(2)-put" % cnt)
            return -1
        cnt = RefCounter.get("a/b", False)
        if cnt != 2:
            print("Error: count = %d != 2 after set(2)-put-get" % cnt)
            return -1
        RefCounter.set("a/b", 100, 0)
        (cnt, bcnt) = RefCounter.check("a/b")
        if cnt != 100 or bcnt != 0:
            print("Error: cnt,bcnt = (%d,%d) != (100,0) after set(100,0)" % \
                    (cnt, bcnt))
            return -1
        cnt = RefCounter.get("a/b", False)
        if cnt != 101:
            print("Error: count = %d != 101 after get" % cnt)
            return -1
        RefCounter.set("a/b", 100, 1)
        (cnt, bcnt) = RefCounter.check("a/b")
        if cnt != 100 or bcnt != 1:
            print("Error: cnt,bcnt = (%d,%d) != (100,1) after set(100,1)" % \
                    (cnt, bcnt))
            return -1
        RefCounter.reset("a/b")
        (cnt, bcnt) = RefCounter.check("a/b")
        if cnt != 0:
            print("Error: check = %d != 0 after reset" % cnt)
            return -1

        # binary
        cnt = RefCounter.get("A", True)
        if cnt != 1:
            print("Error: count = %d != 1 after get(bin)" % cnt)
            return -1
        cnt = RefCounter.get("A", True)
        if cnt != 1:
            print("Error: count = %d != 1 after get(bin)*2" % cnt)
            return -1
        cnt = RefCounter.put("A", True)
        if cnt != 0:
            print("Error: count = %d != 0 after get(bin)*2-put(bin)" % cnt)
            return -1
        cnt = RefCounter.put("A", True)
        if cnt != 0:
            print("Error: count = %d != 0 after get(bin)*2-put(bin)*2" % cnt)
            return -1
        try:
            RefCounter.set("A", 0, 2)
            print("Error: set(0,2) was allowed")
            return -1
        except RefCounterException:
            pass
        cnt = RefCounter.get("A", True)
        if cnt != 1:
            print("Error: count = %d != 1 after get(bin)" % cnt)
            return -1
        cnt = RefCounter.get("A", False)
        if cnt != 2:
            print("Error: count = %d != 2 after get(bin)-get" % cnt)
            return -1
        cnt = RefCounter.get("A", False)
        if cnt != 3:
            print("Error: count = %d != 3 after get(bin)-get-get" % cnt)
            return -1
        cnt = RefCounter.get("A", True)
        if cnt != 3:
            print("Error: count = %d != 3 after get(bin)-get*2-get(bin)" % cnt)
            return -1
        cnt = RefCounter.put("A", False)
        if cnt != 2:
            print("Error: count = %d != 2 after get(bin)*2-get*2-put" % cnt)
            return -1
        cnt = RefCounter.put("A", True)
        if cnt != 1:
            print("Error: cnt = %d != 1 after get(b)*2-get*2-put-put(b)" % cnt)
            return -1
        cnt = RefCounter.put("A", False)
        if cnt != 0:
            print("Error: cnt = %d != 0 after get(b)*2-get*2-put*2-put(b)" % cnt)
            return -1

        # names
        cnt = RefCounter.get("Z", False)
        if cnt != 1:
            print("Error: count = %d != 1 after get (no ns 1)" % cnt)
            return -1

        cnt = RefCounter.get("Z/", False)
        if cnt != 1:
            print("Error: count = %d != 1 after get (no ns 2)" % cnt)
            return -1

        cnt = RefCounter.get("/Z", False)
        if cnt != 1:
            print("Error: count = %d != 1 after get (no ns 3)" % cnt)
            return -1

        cnt = RefCounter.get("/Z/*/?/\\", False)
        if cnt != 1:
            print("Error: count = %d != 1 after get (no ns 4)" % cnt)
            return -1

        cnt = RefCounter.get("Z", False)
        if cnt != 2:
            print("Error: count = %d != 2 after get (no ns 1)" % cnt)
            return -1

        cnt = RefCounter.get("Z/", False)
        if cnt != 2:
            print("Error: count = %d != 2 after get (no ns 2)" % cnt)
            return -1

        cnt = RefCounter.get("/Z", False)
        if cnt != 2:
            print("Error: count = %d != 2 after get (no ns 3)" % cnt)
            return -1

        cnt = RefCounter.get("/Z/*/?/\\", False)
        if cnt != 2:
            print("Error: count = %d != 2 after get (no ns 4)" % cnt)
            return -1

        # resetAll
        RefCounter.resetAll("B")
        cnt = RefCounter.get("Z", False, "B")
        if cnt != 1:
            print("Error: count = %d != 1 after resetAll-get" % cnt)
            return -1

        cnt = RefCounter.get("Z", False, "C")
        if cnt != 1:
            print("Error: count = %d != 1 after C.get" % cnt)
            return -1

        RefCounter.resetAll("B")
        cnt = RefCounter.get("Z", False, "B")
        if cnt != 1:
            print("Error: count = %d != 1 after second resetAll-get" % cnt)
            return -1

        cnt = RefCounter.get("Z", False, "C")
        if cnt != 2:
            print("Error: count = %d != 2 after second C.get" % cnt)
            return -1

        RefCounter.resetAll("D")
        RefCounter.resetAll()
        cnt = RefCounter.put("Z", False, "B")
        if cnt != 0:
            print("Error: count = %d != 0 after resetAll-put" % cnt)
            return -1

        cnt = RefCounter.put("Z", False, "C")
        if cnt != 0:
            print("Error: count = %d != 0 after C.resetAll-put" % cnt)
            return -1

        RefCounter.resetAll()

        return 0
    _runTests = staticmethod(_runTests)

class LVInfo:
    def __init__(self, name):
        self.name = name
        self.size = 0
        self.active = False
        self.open = 0
        self.readonly = False
        self.tags = []

    def toString(self):
        return "%s, size=%d, active=%s, open=%s, ro=%s, tags=%s" % \
                (self.name, self.size, self.active, self.open, self.readonly, \
                self.tags)


def lazyInit(op):
    def wrapper(self, *args):
        if not self.initialized:
            util.SMlog("LVMCache: will initialize now")
            self.refresh()
            #util.SMlog("%s(%s): %s" % (op, args, self.toString()))
        try:
            ret = op(self, * args)
        except KeyError:
            util.logException("LVMCache")
            util.SMlog("%s(%s): %s" % (op, args, self.toString()))
            raise
        return ret
    return wrapper


class LVMCache:
    """Per-VG object to store LV information. Can be queried for cached LVM
    information and refreshed"""

    def __init__(self, vgName):
        """Create a cache for VG vgName, but don't scan the VG yet"""
        self.vgName = vgName
        self.vgPath = "/dev/%s" % self.vgName
        self.lvs = dict()
        self.tags = dict()
        self.initialized = False
        util.SMlog("LVMCache created for %s" % vgName)

    def refresh(self):
        """Get the LV information for the VG using "lvs" """
        util.SMlog("LVMCache: refreshing")
        #cmd = lvutil.cmd_lvm([lvutil.CMD_LVS, "--noheadings", "--units",
        #                    "b", "-o", "+lv_tags", self.vgPath])
        #text = util.pread2(cmd)

        cmd = [lvutil.CMD_LVS, "--noheadings", "--units",
                               "b", "-o", "+lv_tags", self.vgPath]

        text = lvutil.cmd_lvm(cmd)
        self.lvs.clear()
        self.tags.clear()
        for line in text.split('\n'):
            if not line:
                continue
            fields = line.split()
            lvName = fields[0]
            lvInfo = LVInfo(lvName)
            lvInfo.size = int(fields[3].replace("B", ""))
            lvInfo.active = (fields[2][4] == 'a')
            if (fields[2][5] == 'o'):
                lvInfo.open = 1
            lvInfo.readonly = (fields[2][1] == 'r')
            self.lvs[lvName] = lvInfo
            if len(fields) >= 5:
                tags = fields[4].split(',')
                for tag in tags:
                    self._addTag(lvName, tag)
        self.initialized = True

    #
    # lvutil functions
    #
    @lazyInit
    def create(self, lvName, size, tag=None):
        lvutil.create(lvName, size, self.vgName, tag)
        lvInfo = LVInfo(lvName)
        lvInfo.size = size
        lvInfo.active = True
        self.lvs[lvName] = lvInfo
        if tag:
            self._addTag(lvName, tag)

    @lazyInit
    def remove(self, lvName):
        path = self._getPath(lvName)
        lvutil.remove(path)
        for tag in self.lvs[lvName].tags:
            self._removeTag(lvName, tag)
        del self.lvs[lvName]

    @lazyInit
    def rename(self, lvName, newName):
        path = self._getPath(lvName)
        lvutil.rename(path, newName)
        lvInfo = self.lvs[lvName]
        del self.lvs[lvName]
        lvInfo.name = newName
        self.lvs[newName] = lvInfo

    @lazyInit
    def setSize(self, lvName, newSize):
        path = self._getPath(lvName)
        size = self.getSize(lvName)
        lvutil.setSize(path, newSize, (newSize < size))
        self.lvs[lvName].size = newSize

    @lazyInit
    def activate(self, ns, ref, lvName, binary):
        lock = Lock(ref, ns)
        lock.acquire()
        try:
            count = RefCounter.get(ref, binary, ns)
            if count == 1:
                try:
                    self.activateNoRefcount(lvName)
                except util.CommandException:
                    RefCounter.put(ref, binary, ns)
                    raise
        finally:
            lock.release()

    @lazyInit
    def deactivate(self, ns, ref, lvName, binary):
        lock = Lock(ref, ns)
        lock.acquire()
        try:
            count = RefCounter.put(ref, binary, ns)
            if count > 0:
                return
            refreshed = False
            while True:
                lvInfo = self.getLVInfo(lvName)
                if len(lvInfo) != 1:
                    raise util.SMException("LV info not found for %s" % ref)
                info = lvInfo[lvName]
                if info.open:
                    if refreshed:
                        # should never happen in normal conditions but in some
                        # failure cases the recovery code may not be able to
                        # determine what the correct refcount should be, so it
                        # is not unthinkable that the value might be out of
                        # sync
                        util.SMlog("WARNING: deactivate: LV %s open" % lvName)
                        return
                    # check again in case the cached value is stale
                    self.refresh()
                    refreshed = True
                else:
                    break
            try:
                self.deactivateNoRefcount(lvName)
            except util.CommandException:
                self.refresh()
                if self.getLVInfo(lvName):
                    util.SMlog("LV %s could not be deactivated" % lvName)
                    if lvInfo[lvName].active:
                        util.SMlog("Reverting the refcount change")
                        RefCounter.get(ref, binary, ns)
                    raise
                else:
                    util.SMlog("LV %s not found" % lvName)
        finally:
            lock.release()

    @lazyInit
    def activateNoRefcount(self, lvName, refresh=False):
        path = self._getPath(lvName)
        lvutil.activateNoRefcount(path, refresh)
        self.lvs[lvName].active = True

    @lazyInit
    def deactivateNoRefcount(self, lvName):
        path = self._getPath(lvName)
        if self.checkLV(lvName):
            lvutil.deactivateNoRefcount(path)
            self.lvs[lvName].active = False
        else:
            util.SMlog("LVMCache.deactivateNoRefcount: no LV %s" % lvName)
            lvutil._lvmBugCleanup(path)

    @lazyInit
    def setHidden(self, lvName, hidden=True):
        path = self._getPath(lvName)
        if hidden:
            lvutil.setHidden(path)
            self._addTag(lvName, lvutil.LV_TAG_HIDDEN)
        else:
            lvutil.setHidden(path, hidden=False)
            self._removeTag(lvName, lvutil.LV_TAG_HIDDEN)

    @lazyInit
    def setReadonly(self, lvName, readonly):
        path = self._getPath(lvName)
        if self.lvs[lvName].readonly != readonly:
            uuids = util.findall_uuid(path)
            ns = NS_PREFIX_LVM + uuids[0]
            # Taking this lock is needed to avoid a race condition
            # with tap-ctl open (which is now taking the same lock)
            lock = Lock("lvchange-p", ns)
            lock.acquire()
            lvutil.setReadonly(path, readonly)
            lock.release()
            self.lvs[lvName].readonly = readonly

    @lazyInit
    def changeOpen(self, lvName, inc):
        """We don't actually open or close the LV, just mark it in the cache"""
        self.lvs[lvName].open += inc

    #
    # cached access
    #
    @lazyInit
    def checkLV(self, lvName):
        return self.lvs.get(lvName)

    @lazyInit
    def getLVInfo(self, lvName=None):
        result = dict()
        lvs = []
        if lvName is None:
            lvs = self.lvs.keys()
        elif self.lvs.get(lvName):
            lvs = [lvName]
        for lvName in lvs:
            lvInfo = self.lvs[lvName]
            lvutilInfo = lvutil.LVInfo(lvName)
            lvutilInfo.size = lvInfo.size
            lvutilInfo.active = lvInfo.active
            lvutilInfo.open = (lvInfo.open > 0)
            lvutilInfo.readonly = lvInfo.readonly
            if lvutil.LV_TAG_HIDDEN in lvInfo.tags:
                lvutilInfo.hidden = True
            result[lvName] = lvutilInfo
        return result

    @lazyInit
    def getSize(self, lvName):
        return self.lvs[lvName].size

    @lazyInit
    def getHidden(self, lvName):
        return (lvutil.LV_TAG_HIDDEN in self.lvs[lvName].tags)

    @lazyInit
    def getTagged(self, tag):
        lvList = self.tags.get(tag)
        if not lvList:
            return []
        return lvList

    @lazyInit
    def is_active(self, lvname):
        return self.lvs[lvname].active

    #
    # private
    #
    def _getPath(self, lvName):
        return os.path.join(self.vgPath, lvName)

    def _addTag(self, lvName, tag):
        self.lvs[lvName].tags.append(tag)
        if self.tags.get(tag):
            self.tags[tag].append(lvName)
        else:
            self.tags[tag] = [lvName]

    def _removeTag(self, lvName, tag):
        self.lvs[lvName].tags.remove(tag)
        self.tags[tag].remove(lvName)

    def toString(self):
        result = "LVM Cache for %s: %d LVs" % (self.vgName, len(self.lvs))
        for lvName, lvInfo in self.lvs.items():
            result += "\n%s" % lvInfo.toString()
        return result

class Flock:
    """A C flock struct."""

    def __init__(self, l_type, l_whence=0, l_start=0, l_len=0, l_pid=0):
        """See fcntl(2) for field details."""
        self.fields = [l_type, l_whence, l_start, l_len, l_pid]

    FORMAT = "hhqql"
    # struct flock(2) format, tested with python2.4/i686 and
    # python2.5/x86_64. http://docs.python.org/lib/posix-large-files.html

    def fcntl(self, fd, cmd):
        """Issues a system fcntl(fd, cmd, self). Updates self with what was
        returned by the kernel. Otherwise raises IOError(errno)."""

        st = struct.pack(self.FORMAT, * self.fields)
        st = fcntl.fcntl(fd, cmd, st)

        fields = struct.unpack(self.FORMAT, st)
        self.__init__( * fields)

    FIELDS = {'l_type': 0,
               'l_whence': 1,
               'l_start': 2,
               'l_len': 3,
               'l_pid': 4}

    def __getattr__(self, name):
        idx = self.FIELDS[name]
        return self.fields[idx]

    def __setattr__(self, name, value):
        idx = self.FIELDS.get(name)
        if idx is None:
            self.__dict__[name] = value
        else:
            self.fields[idx] = value


class FcntlLockBase:
    """Abstract base class for either reader or writer locks. A respective
    definition of LOCK_TYPE (fcntl.{F_RDLCK|F_WRLCK}) determines the
    type."""

    LOCK_TYPE = None

    if __debug__:
        ERROR_ISLOCKED = "Attempt to acquire lock held."
        ERROR_NOTLOCKED = "Attempt to unlock lock not held."

    def __init__(self, fd):
        """Creates a new, unheld lock."""
        self.fd = fd
        #
        # Subtle: fcntl(2) permits re-locking it as often as you want
        # once you hold it. This is slightly counterintuitive and we
        # want clean code, so we add one bit of our own bookkeeping.
        #
        self._held = False

    def lock(self):
        """Blocking lock aquisition."""
        assert not self._held, self.ERROR_ISLOCKED
        Flock(self.LOCK_TYPE).fcntl(self.fd, fcntl.F_SETLKW)
        self._held = True

    def trylock(self):
        """Non-blocking lock aquisition. Returns True on success, False
        otherwise."""
        if self._held:
            return False
        try:
            Flock(self.LOCK_TYPE).fcntl(self.fd, fcntl.F_SETLK)
        except IOError as e:
            if e.errno in [errno.EACCES, errno.EAGAIN]:
                return False
            raise
        self._held = True
        return True

    def held(self):
        """Returns True if @self holds the lock, False otherwise."""
        return self._held

    def unlock(self):
        """Release a previously acquired lock."""
        Flock(fcntl.F_UNLCK).fcntl(self.fd, fcntl.F_SETLK)
        self._held = False

    def test(self):
        """Returns the PID of the process holding the lock or -1 if the lock
        is not held."""
        if self._held:
            return os.getpid()
        flock = Flock(self.LOCK_TYPE)
        flock.fcntl(self.fd, fcntl.F_GETLK)
        if flock.l_type == fcntl.F_UNLCK:
            return -1
        return flock.l_pid


class WriteLock(FcntlLockBase):
    """A simple global writer (i.e. exclusive) lock."""
    LOCK_TYPE = fcntl.F_WRLCK


class LockException(util.SMException):
    pass


class Lock(object):
    """Simple file-based lock on a local FS. With shared reader/writer
    attributes."""

    BASE_DIR = "/var/lock/sm"

    INSTANCES = {}
    BASE_INSTANCES = {}

    def __new__(cls, name, ns=None, *args, **kwargs):
        if ns:
            if ns not in Lock.INSTANCES:
                Lock.INSTANCES[ns] = {}
            instances = Lock.INSTANCES[ns]
        else:
            instances = Lock.BASE_INSTANCES

        if name not in instances:
            instances[name] = LockImplementation(name, ns)
        return instances[name]

    # These are required to pacify pylint as it doesn't understand the __new__
    def acquire(self):
        raise NotImplementedError("Lock methods implemented in LockImplementation")

    def acquireNoblock(self):
        raise NotImplementedError("Lock methods implemented in LockImplementation")

    def release(self):
        raise NotImplementedError("Lock methods implemented in LockImplementation")

    def held(self):
        raise NotImplementedError("Lock methods implemented in LockImplementation")

    def _mknamespace(ns):

        if ns is None:
            return ".nil"

        assert not ns.startswith(".")
        assert ns.find(os.path.sep) < 0
        return ns
    _mknamespace = staticmethod(_mknamespace)

    @staticmethod
    def clearAll():
        """
        Drop all lock instances, to be used when forking, but not execing
        """
        Lock.INSTANCES = {}
        Lock.BASE_INSTANCES = {}

    def cleanup(name, ns=None):
        if ns:
            if ns in Lock.INSTANCES:
                if name in Lock.INSTANCES[ns]:
                    del Lock.INSTANCES[ns][name]
                if len(Lock.INSTANCES[ns]) == 0:
                    del Lock.INSTANCES[ns]
        elif name in Lock.BASE_INSTANCES:
            del Lock.BASE_INSTANCES[name]

        ns = Lock._mknamespace(ns)
        path = os.path.join(Lock.BASE_DIR, ns, name)
        if os.path.exists(path):
            Lock._unlink(path)

    cleanup = staticmethod(cleanup)

    def cleanupAll(ns=None):
        ns = Lock._mknamespace(ns)
        nspath = os.path.join(Lock.BASE_DIR, ns)

        if not os.path.exists(nspath):
            return

        for file in os.listdir(nspath):
            path = os.path.join(nspath, file)
            Lock._unlink(path)

        Lock._rmdir(nspath)

    cleanupAll = staticmethod(cleanupAll)
    #
    # Lock and attribute file management
    #

    def _mkdirs(path):
        """Concurrent makedirs() catching EEXIST."""
        if os.path.exists(path):
            return
        try:
            os.makedirs(path)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise LockException("Failed to makedirs(%s)" % path)
    _mkdirs = staticmethod(_mkdirs)

    def _unlink(path):
        """Non-raising unlink()."""
        util.SMlog("lock: unlinking lock file %s" % path)
        try:
            os.unlink(path)
        except Exception as e:
            util.SMlog("Failed to unlink(%s): %s" % (path, e))
    _unlink = staticmethod(_unlink)

    def _rmdir(path):
        """Non-raising rmdir()."""
        util.SMlog("lock: removing lock dir %s" % path)
        try:
            os.rmdir(path)
        except Exception as e:
            util.SMlog("Failed to rmdir(%s): %s" % (path, e))
    _rmdir = staticmethod(_rmdir)


class LockImplementation(object):

    def __init__(self, name, ns=None):
        self.lockfile = None

        self.ns = Lock._mknamespace(ns)

        assert not name.startswith(".")
        assert name.find(os.path.sep) < 0
        self.name = name

        self.count = 0

        self._open()

    def _open(self):
        """Create and open the lockable attribute base, if it doesn't exist.
        (But don't lock it yet.)"""

        # one directory per namespace
        self.nspath = os.path.join(Lock.BASE_DIR, self.ns)

        # the lockfile inside that namespace directory per namespace
        self.lockpath = os.path.join(self.nspath, self.name)

        number_of_enoent_retries = 10

        while True:
            Lock._mkdirs(self.nspath)

            try:
                self._open_lockfile()
            except IOError as e:
                # If another lock within the namespace has already
                # cleaned up the namespace by removing the directory,
                # _open_lockfile raises an ENOENT, in this case we retry.
                if e.errno == errno.ENOENT:
                    if number_of_enoent_retries > 0:
                        number_of_enoent_retries -= 1
                        continue
                raise
            break

        fd = self.lockfile.fileno()
        self.lock = WriteLock(fd)

    def _open_lockfile(self):
        """Provide a seam, so extreme situations could be tested"""
        util.SMlog("lock: opening lock file %s" % self.lockpath)
        self.lockfile = open(self.lockpath, "w+")

    def _close(self):
        """Close the lock, which implies releasing the lock."""
        if self.lockfile is not None:
            if self.held():
                # drop all reference counts
                self.count = 0
                self.release()
            self.lockfile.close()
            util.SMlog("lock: closed %s" % self.lockpath)
            self.lockfile = None

    __del__ = _close

    def cleanup(self, name, ns=None):
        Lock.cleanup(name, ns)

    def cleanupAll(self, ns=None):
        Lock.cleanupAll(ns)
    #
    # Actual Locking
    #

    def acquire(self):
        """Blocking lock aquisition, with warnings. We don't expect to lock a
        lot. If so, not to collide. Coarse log statements should be ok
        and aid debugging."""
        if not self.held():
            if not self.lock.trylock():
                util.SMlog("Failed to lock %s on first attempt, " % self.lockpath
                       + "blocked by PID %d" % self.lock.test())
                self.lock.lock()
            if VERBOSE:
                util.SMlog("lock: acquired %s" % self.lockpath)
        self.count += 1

    def acquireNoblock(self):
        """Acquire lock if possible, or return false if lock already held"""
        if not self.held():
            exists = os.path.exists(self.lockpath)
            ret = self.lock.trylock()
            if VERBOSE:
                util.SMlog("lock: tried lock %s, acquired: %s (exists: %s)" % \
                        (self.lockpath, ret, exists))
        else:
            ret = True

        if ret:
            self.count += 1

        return ret

    def held(self):
        """True if @self acquired the lock, False otherwise."""
        return self.lock.held()

    def release(self):
        """Release a previously acquired lock."""
        if self.count >= 1:
            self.count -= 1

        if self.count > 0:
            return

        self.lock.unlock()
        if VERBOSE:
            util.SMlog("lock: released %s" % self.lockpath)
