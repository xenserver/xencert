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

"""Storage handler classes for various storage drivers"""
import copy
from threading import Thread
import time
import os
import commands
import glob
import random
import operator
from xml.dom import minidom
import StorageHandlerUtil
from XenCertLog import printout, print_on_same_line, xencert_print
from XenCertCommon import display_operation_status, get_config_with_hidden_password, hide_path_info_password
import scsiutil
import iscsilib
import util
import BaseISCSI
import nfs
from lvhdutil import VG_LOCATION,VG_PREFIX
from lvutil import MDVOLUME_NAME, remove, rename
from srmetadata import LVMMetadataHandler, updateLengthInHeader, open_file, close
import metadata


retValIO = 0
timeTaken = '' 
bytesCopied = ''
speedOfCopy = ''
pathsFailed = False
failoverTime = 0

RPCINFO_BIN = "/usr/sbin/rpcinfo"

# simple tracer
def report(predicate, condition):
    if predicate != condition:
        printout("Condition Failed, check SMlog")

# Hardcoded time limit for Functional tests in hours
timeLimitFunctional = 4

def retval_judge(retval, exception, checkpoint=None, point=None):
    if not retval:
        raise Exception(exception)
    if checkpoint:
        checkpoint += point
    return checkpoint

def result_judge(result, exception, uuid=None):
    if not result:
        raise Exception(exception)
    return uuid

class TimedDeviceIO(Thread):
    def __init__(self, device):
        Thread.__init__(self)
        self.device = device

    def run(self):
        # Sleep for a period of time before checking for any incomplete snapshots to clean.
        devicename = '/dev/' + self.device
        dd_out_file = 'of=' + devicename
        xencert_print("Now copy data from /dev/zero to this device and record the time taken to copy it." )
        cmd = ['dd', 'if=/dev/zero', dd_out_file, 'bs=1M', 'count=1', 'oflag=direct']
        try:
            global retValIO
            global bytesCopied
            global timeTaken
            global speedOfCopy
            retValIO = 0
            timeTaken = '' 
            bytesCopied = ''
            speedOfCopy = ''

            (retValIO, stdout, stderr) = util.doexec(cmd,'')
            if retValIO != 0:
                raise Exception("Disk IO failed for device: %s." % self.device)
            list = stderr.split('\n')
            
            bytesCopied = list[2].split(',')[0]
            timeTaken = list[2].split(',')[1]
            speedOfCopy = list[2].split(',')[2]

            xencert_print("The IO test returned rc: %s stdout: %s, stderr: %s" % (retValIO, stdout, list))
        except Exception, e:
            xencert_print("Could not write through the allocated disk space on test disk, please check the storage configuration manually. Exception: %s" % str(e))

class WaitForFailover(Thread):
    def __init__(self, session, scsiid, active_paths, no_of_paths, checkfunc):
        Thread.__init__(self)        
        self.scsiid = scsiid
        self.active_paths = active_paths
        self.no_of_paths = no_of_paths
        self.checkfunc = checkfunc

    def run(self):
        # Here wait for the expected number of paths to fail.
        global pathsFailed
        global failoverTime
        pathsFailed = False
        failoverTime = 0        
        while not pathsFailed and failoverTime < 50:
            try:
                (retval, list_path_config_new) = StorageHandlerUtil.get_path_status(self.scsiid, True)
                currno_of_paths = (int)(self.active_paths) - len(list_path_config_new)
                if self.checkfunc(currno_of_paths, self.no_of_paths):
                    pathsFailed = True
                time.sleep(1)
                failoverTime += 1                
            except Exception, e:                
                raise Exception(e)
            
class StorageHandler(object):
    KEYS_NOT_POPULATED_BY_THE_STORAGE = ['allowed_operations',
                                         'current_operations',
                                         'VBDs',
                                         'crash_dumps',
                                         'storage_lock',
                                         'parent',
                                         'missing',
                                         'other_config',
                                         'xenstore_data',
                                         'on_boot',
                                         'allow_caching',                                         
                                         'tags',
                                         'metadata_latest',
                                         'metadata_of_pool',
                                         'snapshot_time']
    def __init__(self, storage_conf):
        xencert_print("Reached Storagehandler constructor")
        self.storage_conf = storage_conf
        self.session = util.get_localAPI_session()
        self.sm_config = {}
        self.util_of_param = 'of=%s'
        self.util_pread_cmd = ['dd', 'if=/dev/zero', 'bs=1M', 'count=1', 'oflag=direct']
    
    def perform_sr_trim(self, sr_ref):
        try:
            xencert_print("Calling TRIM plugin on SR: %s" %(sr_ref))
            sr_uuid = self.session.xenapi.SR.get_uuid(sr_ref)
            host_ref = util.get_this_host_ref(self.session)
            return self.session.xenapi.host.call_plugin(host_ref, 'trim', 'do_trim', {'sr_uuid': sr_uuid})
        except Exception, e:
            xencert_print("TRIM tests failed due to exception: %s" %(str(e)))
            return False
    
    def control_path_stress_tests(self):
        sr_ref = None 
        retval = True
        checkpoint = 0
        total_checkpoints = 5
        pbd_plug_unplug_count = 10
        
        try:
            printout("SR CREATION, PBD PLUG-UNPLUG AND SR DELETION TESTS ")
            printout(">> These tests verify the control path by creating an SR, unplugging ")
            printout("   and plugging the PBDs and destroying the SR in multiple iterations. ")
            printout("")
            
            for i in range(0, 10):
                printout("   -> Iteration number: %d " % i)
                total_checkpoints += (2 + pbd_plug_unplug_count)
                (retval, sr_ref, device_config) = self.create()
                checkpoint = retval_judge(retval, "      SR creation failed.       ", checkpoint, 1)
                
                 # Plug and unplug the PBD over multiple iterations
                checkpoint += StorageHandlerUtil.plug_and_unplug_pbds(self.session, sr_ref, pbd_plug_unplug_count)
                
                # destroy the SR
                printout("      Destroy the SR.")
                StorageHandlerUtil.destroy_sr(self.session, sr_ref)
                checkpoint += 1
                    
            printout("SR SPACE AVAILABILITY TEST")
            printout(">> This test verifies that all the free space advertised by an SR")
            printout("   is available and writable.")
            printout("")

            # Create and plug the SR and create a VDI of the maximum space available. Plug the VDI into Dom0 and write data across the whole virtual disk.
            printout("   Create a new SR. ")
            try:
                (retval, sr_ref, device_config) = self.create()
                checkpoint = retval_judge(retval, "      SR creation failed.", checkpoint, 1)
                device_config_tmp = get_config_with_hidden_password(device_config, self.storage_conf['storage_type'])
                xencert_print("Created the SR %s using device_config: %s " % (sr_ref, device_config_tmp))
                display_operation_status(True)
            except Exception, e:
                display_operation_status(False)
                raise e

            (check_point_delta, retval) = StorageHandlerUtil.perform_sr_control_path_tests(self.session, sr_ref)
            checkpoint = retval_judge(retval, "perform_sr_control_path_tests failed. Please check the logs for details. ", checkpoint, check_point_delta)

        except Exception, e: 
            printout("- Control tests failed with an exception. ")
            printout("  Exception: %s" % str(e))
            display_operation_status(False)
            retval = False

        try:
            # Try cleaning up here
            # Execute trim on the SR before destroying based on type
            if sr_ref is not None:
                sr_type = self.session.xenapi.SR.get_type(sr_ref)
                if sr_type in ['lvmoiscsi', 'lvmohba', 'lvmofcoe']:
                    printout("SR SPACE RECLAMATION TEST")
                    # Perform TRIM before destroying SR
                    total_checkpoints += 1
                    trim_status = self.perform_sr_trim(sr_ref)
                    if trim_status:
                        checkpoint += 1
                    printout("      Trim Plugin Status: %s" % (str(trim_status)))
                printout("      Destroy the SR.")
                StorageHandlerUtil.destroy_sr(self.session, sr_ref)
                checkpoint += 1
        except Exception, e:
            printout("- Could not cleanup the objects created during testing, please destroy the SR manually. Exception: %s " % str(e))
            display_operation_status(False)

        xencert_print("Checkpoints: %d, total_checkpoints: %s" % (checkpoint, total_checkpoints))
        return (retval, checkpoint, total_checkpoints)

    def mp_config_verification_tests(self):
        disable_mp = False
        try:
            sr_ref = None
            vdi_ref = None
            vbd_ref = None
            retval =True
            checkpoint = 0
            total_checkpoints = 6
            iteration_count = 100
            
            # Check if block unblock callouts have been defined. Else display an error and fail this test
            if self.storage_conf['pathHandlerUtil'] is None:
                raise Exception("Path handler util not specified for multipathing tests.")

            if not os.path.exists(self.storage_conf['pathHandlerUtil']):
                raise Exception("Path handler util specified for multipathing tests does not exist!")

            is_man_block = os.path.basename(self.storage_conf['pathHandlerUtil']) == "blockunblockpaths"
            if self.storage_conf['storage_type'] == 'hba' and self.storage_conf[
                'pathInfo'] is None and not is_man_block:
                raise Exception("Path related information not specified for storage type hba.")

            if self.storage_conf['count'] is not None:
                iteration_count = int(self.storage_conf['count']) + 1
            
            #1. Enable host Multipathing
            if not StorageHandlerUtil.is_mp_enabled(self.session, util.get_localhost_uuid(self.session)):
                StorageHandlerUtil.enable_multipathing(self.session, util.get_localhost_uuid(self.session))
                disable_mp = True

            #2. Create and plug SR
            printout("CREATING SR")
            (retval, sr_ref, device_config) = self.create()
            checkpoint = retval_judge(retval, "      SR creation failed. ", checkpoint, 1)

            printout("MULTIPATH AUTOMATED PATH FAILOVER TESTING")

            if not self.GetPathStatus(device_config):
                printout("   - Failed to get and display path status.")
            else:
                checkpoint += 1

            printout(">> Starting Random Path Block and Restore Iteration test")
            printout("   This test will choose a random selection of upto (n -1) paths ")
            printout("   of a total of n to block, and verify that the IO continues")
            printout("   i.e. the correct paths are detected as failed, within 50 seconds.")
            printout("   The test then verifies that after unblocking the path, it is ")
            printout("   restored within 2 minutes.\n\n")
            printout("   Path Connectivity Details")
            self.DisplayPathStatus()

            # make sure there are at least 2 paths for the multipath tests to make any sense.
            if len(self.listPathConfig) < 2:
                raise Exception("FATAL! At least 2 paths are required for multipath failover testing, please configure your storage accordingly.")
                
            
            # Calculate the number of active paths here
            self.initial_active_paths = 0
            for tuple in self.listPathConfig:
                if tuple[1] == 'active':
                    self.initial_active_paths += 1
            
            # Now testing failure times for the paths.  
            (retval, vdi_ref, vbd_ref, vdi_size) = StorageHandlerUtil.create_max_size_vdi_and_vbd(self.session, sr_ref)
            checkpoint = retval_judge(retval, "Failed to create max size VDI and VBD.", checkpoint, 2)
           
            global retValIO
            global timeTaken
            global bytesCopied
            global speedOfCopy
            printout("")
            printout("Iteration 1:\n")
            printout(" -> No manual/script blocking of paths.")
            s = TimedDeviceIO(self.session.xenapi.VBD.get_device(vbd_ref))
            s.start()
            s.join()
            
            if retValIO != 0:
                display_operation_status(False)
                raise Exception(" IO tests failed for device: %s" % self.session.xenapi.VBD.get_device(vbd_ref))
            
            initial_data_copy_time = float(timeTaken.split()[0])
            if initial_data_copy_time > 3:
                display_operation_status(False, timeTaken)
                printout("    - The initial data copy is too slow at %s" % timeTaken)
            else:
                printout("    - IO test passed. Time: %s. Data: %s. Throughput: %s" % (timeTaken, '1MB', speedOfCopy))
                display_operation_status(True)
                checkpoint += 1

            if len(self.listPathConfig) > 1:
                for i in range(2, iteration_count):
                    max_time_taken = 0
                    throughput_for_max_time = ''
                    total_checkpoints += 2
                    printout("Iteration %d:\n" % i)

                    if is_man_block:
                        printout(" -> Wait for manually blocking paths")
                        self.wait_manual_block_unblock_paths()
                        devices_to_fail = 1
                        checkfunc = operator.ge
                    else:
                        if not self.RandomlyFailPaths():
                            raise Exception("Failed to block paths.")

                        xencert_print("Dev Path Config = '%s', no of Blocked switch Paths = '%s'" % (
                        self.listPathConfig, self.no_of_paths))

                        # Fail path calculation needs to be done only in case of hba SRs
                        if "blockunblockhbapaths" in \
                                self.storage_conf['pathHandlerUtil'].split('/')[-1]:
                            # Calculate the number of devices to be found after the path block
                            devices_to_fail = (len(self.listPathConfig) / self.noOfTotalPaths) * self.no_of_paths
                            xencert_print("Expected devices to fail: %s" % devices_to_fail)
                        else:
                            devices_to_fail = self.no_of_paths
                        checkfunc = operator.eq

                    s = WaitForFailover(self.session, device_config['SCSIid'], len(self.listPathConfig), devices_to_fail, checkfunc)
                    s.start()

                    while s.isAlive():
                        timeTaken = 0
                        s1 = TimedDeviceIO(self.session.xenapi.VBD.get_device(vbd_ref))
                        s1.start()
                        s1.join()

                        if retValIO != 0:
                            display_operation_status(False)
                            raise Exception(
                                "    - IO test failed for device %s." % self.session.xenapi.VBD.get_device(vbd_ref))
                        else:
                            xencert_print("    - IO test passed. Time: %s. Data: %s. Throughput: %s." % (
                            timeTaken, '1MB', speedOfCopy))

                        if timeTaken > max_time_taken:
                            max_time_taken = timeTaken
                            throughput_for_max_time = speedOfCopy

                    if pathsFailed:
                        printout("    - Paths failover time: %s seconds" % failoverTime)
                        printout("    - Maximum IO completion time: %s. Data: %s. Throughput: %s" % (
                        max_time_taken, '1MB', throughput_for_max_time))
                        display_operation_status(True)
                        checkpoint += 1
                    else:
                        display_operation_status(False)
                        if not is_man_block:
                            self.block_unblock_paths(False, self.storage_conf['pathHandlerUtil'], self.no_of_paths,
                                                     self.blockedpathinfo)
                        raise Exception("    - Paths did not failover within expected time.")

                    if is_man_block:
                        printout(" -> Wait for manually unblocking paths and restoration")
                        self.wait_manual_block_unblock_paths()
                    else:
                        self.block_unblock_paths(False, self.storage_conf['pathHandlerUtil'], self.no_of_paths,
                                                 self.blockedpathinfo)
                        printout(" -> Unblocking paths, waiting for restoration.")

                    count = 0
                    paths_match = False
                    while not paths_match and count < 120:
                        paths_match = self.do_new_paths_match(device_config)
                        time.sleep(1)
                        count += 1

                    if not paths_match:
                        display_operation_status(False, "> 2 mins")
                        retval = False
                        raise Exception("The path restoration took more than 2 mins.")
                    else:
                        display_operation_status(True, " " + str(count) + " seconds")
                        checkpoint += 1


            printout("- Test succeeded.")
 
        except Exception, e:
            printout("- There was an exception while performing multipathing configuration tests.")
            printout("  Exception: %s" % str(e))
            display_operation_status(False)
            retval = False

        try:
            # Try cleaning up here
            if vbd_ref is not None:
                self.session.xenapi.VBD.unplug(vbd_ref)
                xencert_print("Unplugged VBD %s " % vbd_ref)
                self.session.xenapi.VBD.destroy(vbd_ref)
                xencert_print("Destroyed VBD %s " % vbd_ref)

            if vdi_ref is not None:
                self.session.xenapi.VDI.destroy(vdi_ref)
                xencert_print("Destroyed VDI %s " % vdi_ref)

            # Try cleaning up here
            if sr_ref is not None:
                printout("      Destroy the SR. ")
                StorageHandlerUtil.destroy_sr(self.session, sr_ref)

                # If multipath was enabled by us, disable it, else continue.
            if disable_mp:
                StorageHandlerUtil.disable_multipathing(self.session, util.get_localhost_uuid(self.session))
                
            checkpoint += 1
                
        except Exception, e:
            printout("- Could not cleanup the objects created during testing, VBD: %s VDI:%s SR:%s. Please destroy the objects manually. Exception: %s" % (vbd_ref, vdi_ref, sr_ref, str(e)))
            display_operation_status(False)

        xencert_print("Checkpoints: %d, total_checkpoints: %s " % (checkpoint, total_checkpoints))
        return (retval, checkpoint, total_checkpoints)

    def get_sr_information(self, map, device_config):
        scsi_id_to_use = None
        for iqn in map.keys():
            for scsi_id in map[iqn]:
                try:
                    device_config['targetIQN'] = iqn
                    device_config['SCSIid'] = scsi_id
                    sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0',
                                                           'XenCertTestSR', '', 'lvmoiscsi', '', False, {})
                    device_config_tmp = get_config_with_hidden_password(device_config,
                                                                        self.storage_conf['storage_type'])
                    xencert_print("Created the SR %s using device_config: %s " % (sr_ref, device_config_tmp))
                    scsi_id_to_use = scsi_id
                    break
                except Exception:
                    xencert_print(
                        "SR creation failed with iqn: %s, and SCSI id: %s, trying the next lun." % (iqn, scsi_id))
            if scsi_id_to_use is None:
                xencert_print("Could not create an SR with any LUNs for IQN %s, trying with other IQNs." % iqn)
            else:
                xencert_print("Created the SR with IQN %s, and SCSIid %s so exiting the loop." % (iqn, scsi_id_to_use))
                break
        if scsi_id_to_use is None:
            xencert_print("Could not create an SR with any IQNs." % iqn)
            raise Exception("Could not create any SRs with the IQN %s." % iqn)
        return (device_config, sr_ref)

    def data_performance_tests(self):
        try:
            sr_ref = None
            vdi_ref1 = None
            vdi_ref2 = None
            vbd_ref1 = None
            vbd_ref2 = None

            #1. Create and plug SR
            xencert_print("First use XAPI to get information for creating an SR.")
            (retval, map_iqn_to_list_portal, map_iqn_to_list_scsi_id) = self.GetIqnPortalScsiIdMap(self.storage_conf['target'], self.storage_conf['chapuser'], self.storage_conf['chappasswd'])

            device_config = {}
            device_config['target'] = self.storage_conf['target']
            if self.storage_conf['chapuser'] is not None and self.storage_conf['chappasswd'] is not None:
                   device_config['chapuser'] = self.storage_conf['chapuser']
                   device_config['chappassword'] = self.storage_conf['chappasswd']

            xencert_print("First use XAPI to get information for creating an SR.")
            (device_config, sr_ref) = self.get_sr_information(map_iqn_to_list_scsi_id, device_config)

            # Now create 2 VDIs of 10GiB each
            # Populate VDI args
            args={}
            args['name_label'] = 'XenCertTestVDI1'
            args['SR'] = sr_ref
            args['name_description'] = ''
            args['virtual_size'] = '1073741824'
            args['type'] = 'user'
            args['sharable'] = False
            args['read_only'] = False
            args['other_config'] = {}
            args['sm_config'] = {}
            args['xenstore_data'] = {}
            args['tags'] = []
            xencert_print("The VDI create parameters are %s" % args)
            vdi_ref1 = self.session.xenapi.VDI.create(args)
            xencert_print("Created new VDI %s" % vdi_ref1)
            printout(" - Create a VDI on this SR, of size 1GiB.")

            # Populate VDI args
            args={}
            args['name_label'] = 'XenCertTestVDI2'
            args['SR'] = sr_ref
            args['name_description'] = ''
            args['virtual_size'] = '1073741824'
            args['type'] = 'user'
            args['sharable'] = False
            args['read_only'] = False
            args['other_config'] = {}
            args['sm_config'] = {}
            args['xenstore_data'] = {}
            args['tags'] = []
            xencert_print("The VDI create parameters are %s" % args)
            vdi_ref2 = self.session.xenapi.VDI.create(args)
            xencert_print("Created new VDI %s" % vdi_ref2)
            printout(" - Create another VDI on this SR, of size 1GiB.")

        except Exception:
            printout("There was an exception while performing multipathing configuration tests.")

        try:
            # Try cleaning up here
            self.vbd_ref_cleanup(vbd_ref1, vdi_ref1)
            self.vbd_ref_cleanup(vbd_ref2, vdi_ref2)

            if sr_ref is not None:
                # First get the PBDs
                pbds = self.session.xenapi.SR.get_PBDs(sr_ref)
                xencert_print("Got the list of pbds for the sr %s as %s" % (sr_ref, pbds))
                xencert_print(" - Now unplug and destroy PBDs for the SR.")
                for pbd in pbds:
                    xencert_print("Looking at PBD: %s" % pbd)
                    self.session.xenapi.PBD.unplug(pbd)
                    self.session.xenapi.PBD.destroy(pbd)
                xencert_print(" - Now forget the SR.")
                xencert_print(" - Now forget the SR: %s" % sr_ref)
                self.session.xenapi.SR.forget(sr_ref)
        except Exception:
            printout("Could not cleanup the objects created during testing, please destroy the SR manually.")

    def find_scsi_key(self, other_config, device_config, ref_other_config):
        for key in other_config.keys():
            if key.find(device_config['SCSIid']):
                printout(
                    "      %-50s %-10s " % (util.get_localhost_uuid(self.session), ref_other_config[key]))
            break

    def pbds_function(self, sr_ref, my_pbd, device_config, ref_other_config):
        pbds = self.session.xenapi.SR.get_PBDs(sr_ref)
        for pbd in pbds:
            if pbd == my_pbd:
                continue
            else:
                host_ref = self.session.xenapi.PBD.get_host(pbd)
                if StorageHandlerUtil.is_mp_enabled(self.session, host_ref):
                    other_config = self.session.xenapi.PBD.get_other_config(pbd)
                    self.find_scsi_key(other_config, device_config, ref_other_config)

    def pool_tests(self):
        try:
            sr_ref = None
            retval = True
            checkpoint = 0
            total_checkpoints = 4

            #1. Enable host Multipathing
            printout("POOL CONSISTENCY TESTS.")
            printout(">> This test creates shared SRs and verifies that PBD records ")
            printout("   display the same number of paths for each host in the pool.")
            printout("   -> Enabling multipathing on each host in the pool.")
            host_disable_mp_list = []
            host_list = self.session.xenapi.host.get_all()
            for host in host_list:
                if not StorageHandlerUtil.is_mp_enabled(self.session, host):
                    StorageHandlerUtil.enable_multipathing(self.session, host)
                    host_disable_mp_list.append(host)

            display_operation_status(True)
            checkpoint += 1

            #Create and plug SR
            xencert_print( "2. Now use XAPI to get information for creating an SR."           )
            printout("   -> Creating shared SR.")
            (retval, sr_ref, device_config) = self.create()
            checkpoint = retval_judge(retval, "      SR creation failed.  ", checkpoint, 1)
                    
            # Now check PBDs for this SR and make sure all PBDs reflect the same number of active and passive paths for hosts with multipathing enabled.  
            printout("   -> Checking paths reflected on PBDs for each host.")
            my_pbd = util.find_my_pbd(self.session, util.get_localhost_uuid(self.session), sr_ref)
            ref_other_config = self.session.xenapi.PBD.get_other_config(my_pbd)
            printout("      %-50s %-10s" % ('Host', '[Active, Passive]'))
            for key in ref_other_config.keys():
                if device_config.has_key('SCSIid') and (key.find(device_config['SCSIid']) != -1):
                    printout("      %-50s %-10s" % (util.get_localhost_uuid(self.session), ref_other_config[key]))
                    break

            self.pbds_function(sr_ref, my_pbd, device_config, ref_other_config)
            display_operation_status(True)
            checkpoint += 1
 
        except Exception, e:
            printout("      There was an exception while performing pool consistency tests. Exception: %s. Please check the log for details." % str(e))
            display_operation_status(False)
            retval = False

        try:
            # Try cleaning up here
            if sr_ref is not None:
                printout("      Destroy the SR. ")
                StorageHandlerUtil.destroy_sr(self.session, sr_ref)                
            checkpoint += 1

        except Exception, e:
            printout("Could not cleanup the SR created during testing, SR: %s. Exception: %s. Please destroy the SR manually." % (sr_ref, str(e)))
            display_operation_status(False)

        printout("   -> Disable multipathing on hosts. ")
        printout(" ")
        for host in host_disable_mp_list:
            StorageHandlerUtil.disable_multipathing(self.session, host)
        
        xencert_print("Checkpoints: %d, total_checkpoints: %s " % (checkpoint, total_checkpoints))
        return (retval, checkpoint, total_checkpoints)
    
    def data_integrity_tests(self):
        printout("DataTests not applicable to %s SR type." % self.storage_conf['storage_type'].upper())
        return (True, 1, 1) 

    # block_or_unblock = True for block, False for unblock
    def block_unblock_paths(self, block_or_unblock, script, no_of_paths, passthrough):
        try:
            stdout = ''
            if block_or_unblock:
                cmd = [os.path.join(os.getcwd(), script), 'block', str(no_of_paths), passthrough]
            else:
                cmd = [os.path.join(os.getcwd(), script), 'unblock', str(no_of_paths), passthrough]
            
            (rc, stdout, stderr) = util.doexec(cmd,'')

            stdout_print = hide_path_info_password(stdout) if self.storage_conf['storage_type'] == 'hba' else stdout
            xencert_print("The path block/unblock utility returned rc: %s stdout: '%s', stderr: '%s'" % (rc, stdout_print, stderr))
            if rc != 0:
                raise Exception("   - The path block/unblock utility returned an error: %s." % stderr)
            return stdout
        except Exception, e:            
            raise Exception(e)

    def wait_manual_block_unblock_paths(self):
        try:
            cmd = [self.storage_conf['pathHandlerUtil']]
            (rc, stdout, stderr) = util.doexec(cmd, '')
            xencert_print(
                "The path manually block/unblock utility returned rc: %s stdout: '%s', stderr: '%s'" % (rc, stdout, stderr))
            if rc != 0:
                raise Exception("   - The path manually block/unblock utility returned an error: %s." % stderr)
            return stdout
        except Exception, e:
            raise Exception(e)
    
    def __del__(self):
        xencert_print("Reached Storagehandler destructor")
        self.session.xenapi.session.logout() 
        
    def create(self):
        # This class specific function will create an SR of the required type and return the required parameters.
        xencert_print("Reached StorageHandler Create")
        
    def do_new_paths_match(self, device_config):
        try:
            # get new config
            (retval, list_path_config_new) = StorageHandlerUtil.get_path_status(device_config['SCSIid'])
            xencert_print("listpathconfig: %s" % self.listPathConfig)
            xencert_print("listpathconfigNew: %s" % list_path_config_new)
            retval_judge(retval, "     - Failed to get path status information for SCSI Id: %s" % device_config['SCSIid'])
            
            # Find new number of active paths
            new_active_paths = 0
            for tuple in list_path_config_new:
                if tuple[1] == 'active':
                    new_active_paths += 1
            
            if new_active_paths < self.initial_active_paths:                            
                    return False
            return True
        except Exception:
            xencert_print("Failed to match new paths with old paths.")
            return False
        
    def populate_vdi_xapi_fields(self, vdi_ref):
        fields = self.session.xenapi.VDI.get_all_records()[vdi_ref]
        for key in self.KEYS_NOT_POPULATED_BY_THE_STORAGE:
            del fields[key]            
            
        return fields
    
    def check_metadata_vdi(self, vdi_ref):
        return
    
    #
    #  VDI related
    #       
    def create_vdi(self, sr_ref, size, name_label = ''):
        xencert_print("Create VDI")
        vdi_rec = {}
        try:
            if name_label != '':
                vdi_rec['name_label'] = name_label
            else:
                vdi_rec['name_label'] = \
                    "XenCertVDI-" + str(time.time()).replace(".","")
            vdi_rec['name_description'] = ''
            vdi_rec['type'] = 'user'
            vdi_rec['virtual_size'] = str(size)
            vdi_rec['SR'] = sr_ref
            vdi_rec['read_only'] = False
            vdi_rec['sharable'] = False
            vdi_rec['other_config'] = {}
            vdi_rec['sm_config'] = {}
            results = self.session.xenapi.VDI.create(vdi_rec)
            self.check_metadata_vdi(results)

            return (True, results)
        except Exception, e:
            xencert_print("Failed to create VDI. Exception: %s" % str(e))
            return (False, str(e))
       
    def resize_vdi(self, vdi_ref, size):
        xencert_print("Resize VDI")
        try:
            self.session.xenapi.VDI.resize(vdi_ref, str(size))
        except Exception, e:
            xencert_print("Failed to Resize VDI. Exception: %s" % str(e))
            raise

    def snapshot_vdi(self, vdi_ref):
        xencert_print("Snapshot VDI")
        options = {}
        try:
            results = self.session.xenapi.VDI.snapshot(vdi_ref, options)
            self.check_metadata_vdi(results)
            return (True, results)
        except Exception, e:
            xencert_print("Failed to Snapshot VDI. Exception: %s" % str(e))
            return (False, str(e))
       
    def clone_vdi(self, vdi_ref):
        xencert_print("Clone VDI")
        options = {}
        try:
            results = self.session.xenapi.VDI.clone(vdi_ref, options)
            self.check_metadata_vdi(results)
            return (True, results)
        except Exception, e:
            xencert_print("Failed to Clone VDI. Exception: %s" % str(e))
            return (False, "")
       
    def destroy_vdi(self, vdi_ref):
        xencert_print("Destroy VDI")
        try:
            results = self.session.xenapi.VDI.destroy(vdi_ref)
            try:
                self.check_metadata_vdi(results)
            except:
                return True
        except Exception, e:
            xencert_print("Failed to Destroy VDI. Exception: %s" % str(e))
            raise Exception("Failed to Destroy VDI. Exception: %s" % str(e))
        
    #
    #  SR related
    #       
    def create_pbd(self, sr_ref, pbd_device_config, host_ref=""):
        try:
            xencert_print("Creating PBD")
            fields = {}
            if not host_ref:
                fields['host'] = util.get_localhost_uuid(self.session)
            else:
                fields['host'] = host_ref
            fields['device_config'] = pbd_device_config
            fields['SR'] = sr_ref
            pbd_ref = self.session.xenapi.PBD.create(fields)
            return pbd_ref
        except Exception, e:
            xencert_print("Failed to create pbd. Exception: %s" % str(e))
            return False
       
    def unplug_pbd(self, pbd_ref):
        try:
            xencert_print("Unplugging PBD")
            self.session.xenapi.PBD.unplug(pbd_ref)
            return True
        except Exception, e:
            xencert_print("Failed to unplug PBD. Exception: %s" % str(e))
            return False
       
    def plug_pbd(self, pbd_ref):
        try:
            xencert_print("Plugging PBD")
            self.session.xenapi.PBD.plug(pbd_ref)
            return True
        except Exception, e:
            xencert_print("Failed to plug PBD. Exception: %s" % str(e))
            return False
       
    def destroy_pbd(self, pbd_ref):
        try:
            xencert_print("destroying PBD")
            self.session.xenapi.PBD.destroy(pbd_ref)
            return True
        except Exception, e:
            xencert_print("Failed to Destroy PBD. Exception: %s" % str(e))
            return False
        
    def forget_sr(self, sr_ref):
        xencert_print("Forget SR")
        try:
            pbd_list = self.session.xenapi.SR.get_PBDs(sr_ref)
            for pbd_ref in pbd_list:
                self.unplug_pbd(pbd_ref)
            for pbd_ref in pbd_list:
                self.destroy_pbd(pbd_ref)
            self.session.xenapi.SR.forget(sr_ref)
            return True
        except Exception, e:
            xencert_print("Failed to Forget SR. Exception: %s" % str(e))
            return False

    def introduce_sr(self, sr_uuid, sr_type):
        xencert_print("Introduce SR")
        try:
            self.session.xenapi.SR.introduce(sr_uuid, 'XenCertTestSR', '', sr_type, '', False, {})
        except Exception, e:
            xencert_print("Failed to Introduce the SR. Exception: %s" % str(e))
            return False
            

    def destroy_sr(self, sr_ref):
        xencert_print("Destroy SR")
        if sr_ref is None:
            return        
        try:
            pbd_list = self.session.xenapi.SR.get_PBDs(sr_ref)
            for pbd_ref in pbd_list:
                self.unplug_pbd(pbd_ref)
            self.session.xenapi.SR.destroy(sr_ref)
            return True
        except Exception, e:
            xencert_print("Failed to Destroy SR. Exception: %s" % str(e))
            raise Exception("Failed to Destroy SR. Exception: %s" % str(e))
        
    def meta_data_tests(self):
        printout("meta_data_tests not applicable to %s SR type." % self.storage_conf['storage_type'].upper())
        return (True, 1, 1)

    def detach_sr(self):
        for pbd in self.session.xenapi.SR.get_PBDs(self.sr_ref):
            self.unplug_pbd(pbd)
        display_operation_status(True)

    def attach_sr(self):
        for pbd in self.session.xenapi.SR.get_PBDs(self.sr_ref):
            self.plug_pbd(pbd)
        display_operation_status(True)

    def detach_destroy_sr(self):
        old_config = {}
        for pbd in self.session.xenapi.SR.get_PBDs(self.sr_ref):
            # save the device_config for pbd creation later
            host = self.session.xenapi.PBD.get_host(pbd)
            old_config[host] = self.session.xenapi.PBD.get_device_config(pbd)
            self.unplug_pbd(pbd)
            self.destroy_pbd(pbd)
        display_operation_status(True)
        return old_config

    def delete_vdi(self):
        for vdi in self.session.xenapi.SR.get_VDIs(self.sr_ref):
            if self.session.xenapi.VDI.get_managed(vdi):
                self.destroy_vdi(vdi)
        display_operation_status(True)


    def metadata_sr_attach_tests(self):
        try:
            retval = True
            self.sr_ref = None
            vdi_ref1 = None
            vdi_ref2 = None
            vdi_ref3 = None
            fd = -1
            try:
                result = True
                printout(">> SR ATTACH ")
                printout(">>> 1. Metadata volume present but is of an older version.")
                printout(">>>>     Create a SR")
                (retval, self.sr_ref, device_config) = self.create()
                retval_judge(retval, "      SR creation failed.  ")
                self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
                display_operation_status(True)                

                printout(">>>>     Add 3 VDIs")
                (result, vdi_ref1) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_1")
                vdi_uuid1 = result_judge(result, "Failed to create VDI. Error: %s" % vdi_ref1, self.session.xenapi.VDI.get_uuid(vdi_ref1))
                (result, vdi_ref2) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_2")
                vdi_uuid2 = result_judge(result, "Failed to create VDI. Error: %s" % vdi_ref2, self.session.xenapi.VDI.get_uuid(vdi_ref2))
                (result, vdi_ref3) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_3")
                vdi_uuid3 = result_judge(result, "Failed to create VDI. Error: %s " % vdi_ref3, self.session.xenapi.VDI.get_uuid(vdi_ref3))
                display_operation_status(True)

                # update the metadata file manually to
                # bring down the metadata version to 1.0
                # update the length to just the SR information length - 2048
                printout(">>>>     Downgrade metadata version, and set length to 2048")
                fd = open_file(self.mdpath, True)
                updateLengthInHeader(fd, 2048, 1,0)
                if fd != -1:
                    close(fd)
                    fd = -1
                display_operation_status(True)

                # detach the SR
                printout(">>>>     Detach the SR")
                self.detach_sr()

                # attach the SR again
                printout(">>>>     Attach the SR")
                self.attach_sr()

                # make sure all the VDIs are created in metadata and match the information in XAPI
                printout(">>>>     Make sure all the VDIs are created in metadata and match the information in XAPI")
                self.verify_vdis_in_metadata(self.mdpath, [vdi_uuid1, vdi_uuid2, \
                                                        vdi_uuid3])
                display_operation_status(True)

                printout(">>> 2. Metadata volume not present - upgrade from a Citrix Hypervisor version which does not have SR metadata volume")

                # detach the SR
                printout(">>>>     Detach the SR")
                self.detach_sr()

                # remove the MGT LV from the storage
                printout(">>>>     Remove the management volume from the storage")
                self.remove_mgt_volume()
                display_operation_status(True)

                # attach the SR
                printout(">>>>     Attach the SR")
                self.attach_sr()

                # make sure all the VDIs are created in metadata and match the information in XAPI
                printout(">>>>     Make sure all the VDIs are created in metadata and match the information in XAPI")
                self.set_md_path()
                self.verify_vdis_in_metadata(self.mdpath, [vdi_uuid1, vdi_uuid2, \
                                                        vdi_uuid3])
                display_operation_status(True)

                # Forget the SR, introduce and attach again
                printout(">>> 3. Forget the SR, introduce and attach again")

                # detach the SR
                printout(">>>>     Detach the SR ")
                old_config = self.detach_destroy_sr()

                # forget the SR
                printout(">>>>     Forget the SR")
                self.session.xenapi.SR.forget(self.sr_ref)
                display_operation_status(True)

                # introduce the SR
                printout(">>>>     Introduce the SR")
                self.sr_ref = self.session.xenapi.SR.introduce(self.sr_uuid, \
                                'XenCertTestSR', '', 'lvmoiscsi', '', True, {})
                self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
                display_operation_status(True)

                # attach the SR
                printout(">>>>     Attach the SR ")
                for host,config in old_config.items():
                    pbd = self.create_pbd(self.sr_ref, config, host)
                    self.plug_pbd(pbd)
                display_operation_status(True)

                # scan the SR
                printout(">>>>     Scan the SR")
                self.session.xenapi.SR.scan(self.sr_ref)
                display_operation_status(True)

                # make sure all the VDIs are created in metadata and match the information in XAPI
                printout(">>>>     Make sure all the VDIs are created in metadata and match the information in XAPI ")
                self.set_md_path()
                self.verify_vdis_in_metadata(self.mdpath, [vdi_uuid1, vdi_uuid2, \
                                                        vdi_uuid3])
                display_operation_status(True)

                printout(">>>  4. Metadata volume present and if of the correct version")
                printout(">>>>     4.1. One or more VDIs present in metadata but not present in storage anymore.")

                # Remove storage for the last VDI from the backend
                printout(">>>>>        Remove storage for the last VDI from the backend")
                self.remove_vdi_from_storage(vdi_uuid3)
                display_operation_status(True)

                # detach the SR
                printout(">>>>>        Detach the SR")
                self.detach_sr()

                # attach the SR
                printout(">>>>>        Attach the SR")
                self.attach_sr()

                # the entries for the missing VDIs should be removed from the metadata and XAPI
                printout(">>>>>        Check that the VDI is removed from the metadata.")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1})[1]
                if vdi_info.has_key(vdi_uuid3):
                    raise Exception(" VDI %s found in the metadata, even " \
                            "after the storage was deleted before SR "\
                            "attach." % vdi_uuid3)
                display_operation_status(True)

                # They should not be in XAPI either
                printout(">>>>>        Scan the SR and check that the VDI is removed from XAPI.")
                self.session.xenapi.SR.scan(self.sr_ref)
                vdi_found_in_xapi = False
                try:
                    self.session.xenapi.VDI.get_by_uuid(vdi_uuid3)
                    vdi_found_in_xapi = True
                except:
                    # this is fine, we do not expect it in XAPI
                    pass

                if vdi_found_in_xapi:
                    raise Exception(" VDI %s found in XAPI, even after the " \
                        "storage was deleted before SR attach." % vdi_uuid3)
                else:
                    display_operation_status(True)

            except Exception, e:
                printout("Exception testing metadata SR attach tests. Error: %s" % str(e))
                retval = False
                display_operation_status(False)

        finally:
            if fd != -1:
                close(fd)

            if self.sr_ref is not None:
                printout(">>>> Delete VDIs on the SR ")
                self.delete_vdi()

                printout(">>>> Detach the SR ")
                self.detach_sr()

                printout(">>>> Destroy the SR ")
                self.destroy_sr(self.sr_ref)
                display_operation_status(True)

        return retval

    def metadata_sr_probe_tests(self):
        try:
            retval = True
            self.sr_ref = None
            vdi_ref1 = None
            vdi_ref2 = None
            vdi_ref3 = None
            try:
                result = True
                printout(">> SR PROBE ")
                printout(">>>     Create a SR")
                (retval, self.sr_ref, device_config) = self.create()
                retval_judge(retval, "      SR creation failed.   ")
                self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
                display_operation_status(True)                
                
                printout(">>>     Add 3 VDIs")
                (result, vdi_ref1) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_1")
                result_judge(result, "Failed to create VDI. Error: %s " % vdi_ref1)
                (result, vdi_ref2) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_2")
                result_judge(result, "Failed to create VDI. Error: %s  " % vdi_ref2)
                (result, vdi_ref3) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_3")
                result_judge(result, "Failed to create VDI. Error: %s  " % vdi_ref3)
                display_operation_status(True)
                
                printout(">>>      Run a non-metadata probe")
                output = self.probe_sr()
                display_operation_status(True)
                
                printout(">>>>         Make sure we get only UUID and Devlist, save these.")
                sr_info = self.parse_probe_output(output)
                if len(sr_info.keys()) < 2 or not sr_info.has_key('UUID') or \
                    not sr_info.has_key('Devlist'):
                        raise Exception(" Non-metadata probe returned "\
                                "incorrect details. Probe output: %s" % sr_info)
                else:
                    self.devlist = sr_info['Devlist']
                display_operation_status(True)
                
                printout(">>>      Run a metadata probe")
                self.sm_config['metadata'] = 'true'
                output = self.probe_sr()
                display_operation_status(True)
                
                printout(">>>>         Make sure all parameters are present")
                sr_info = self.parse_probe_output(output)
                if not sr_info.has_key('UUID') or \
                    not sr_info.has_key('Devlist') or \
                    not sr_info.has_key('name_label') or \
                    not sr_info.has_key('name_description') or \
                    not sr_info.has_key('pool_metadata_detected'):
                    raise Exception(" Metadata probe returned incomplete " \
                                    "details. Probe output: %s" % sr_info)
                display_operation_status(True)
                
                printout(">>>>         Make sure all parameters are correct and that pool_metadata_detected is False")
                if sr_info['UUID'] == self.sr_uuid  or \
                    sr_info['Devlist'] == self.devlist or \
                    sr_info['name_label'] == 'XenCertTestSR' or \
                    sr_info['name_description'] == 'XenCertTestSR-desc' or \
                    sr_info['pool_metadata_detected'] == 'false':
                        xencert_print("All parameters validated!")
                else:
                    raise Exception(" Metadata probe returned incomplete " \
                                    "details. Probe output: %s" % sr_info)
                display_operation_status(True)
                
                printout(">>>     Enable database replication on the SR")
                self.session.xenapi.SR.enable_database_replication(self.sr_ref)
                display_operation_status(True)
                
                printout(">>>      Run a metadata probe")
                self.sm_config['metadata'] = 'true'
                output = self.probe_sr()
                display_operation_status(True)
                
                printout(">>>>         Make sure all parameters are present")
                sr_info = self.parse_probe_output(output)
                if not sr_info.has_key('UUID') or \
                    not sr_info.has_key('Devlist') or \
                    not sr_info.has_key('name_label') or \
                    not sr_info.has_key('name_description') or \
                    not sr_info.has_key('pool_metadata_detected'):
                    raise Exception(" Metadata probe returned incomplete  " \
                                    "details. Probe output: %s " % sr_info)
                display_operation_status(True)
                
                printout(">>>>         Make sure all parameters are correct and that pool_metadata_detected is True")
                if sr_info['UUID'] == self.sr_uuid  or \
                    sr_info['Devlist'] == self.devlist or \
                    sr_info['name_label'] == 'XenCertTestSR' or \
                    sr_info['name_description'] == 'XenCertTestSR-desc' or \
                    sr_info['pool_metadata_detected'] == 'true':
                        xencert_print("All parameters validated!")
                else:
                    raise Exception(" Metadata probe returned incomplete  " \
                                    "details. Probe output: %s " % sr_info)
                display_operation_status(True)
                
                printout(">>>     Disable database replication on the SR")
                self.session.xenapi.SR.disable_database_replication(self.sr_ref)
                display_operation_status(True)
                                
            except Exception, e:
                printout("Exception testing metadata SR probe tests. Error: %s" % str(e))
                retval = False
                display_operation_status(False)
        
        finally:
            if self.sr_ref is not None:
                printout(">>>   Delete VDIs on the SR")
                self.delete_vdi()

                printout(">>>   Detach the SR")
                self.detach_sr()
                printout(">>>   Destroy the SR")
                self.destroy_sr(self.sr_ref)
                display_operation_status(True)
        
        return retval

    def metadata_sr_scan_tests(self):
        try:
            retval = True
            self.sr_ref = None
            vdi_ref1 = None
            vdi_ref2 = None
            vdi_ref3 = None
            try:
                result = True
                printout(">> SR SCAN ")
                printout(">>> 1. VDI present in SR metadata but missing from XAPI.")
                printout(">>>>     Create a SR")
                (retval, self.sr_ref, device_config) = self.create()
                retval_judge(retval, "      SR creation failed.   ")
                self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
                display_operation_status(True)                

                printout(">>>>     Add the source VDI")
                (result, vdi_ref1) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_1")
                vdi_uuid1 = result_judge(result, "Failed to create VDI. Error: %s   " % vdi_ref1, self.session.xenapi.VDI.get_uuid(vdi_ref1))
                display_operation_status(True)

                printout(">>>>     Snapshot the source VDI")                
                (result, vdi_ref2) = self.snapshot_vdi(vdi_ref1)
                vdi_uuid2 = result_judge(result, "Failed to snapshot VDI. Error: %s" % vdi_ref2,
                                         self.session.xenapi.VDI.get_uuid(vdi_ref2))
                display_operation_status(True)

                printout(">>>>     Clone the source VDI")
                (result, vdi_ref3) = self.clone_vdi(vdi_ref1)
                result_judge(result, "Failed to snapshot VDI. Error: %s" % vdi_ref3)
                display_operation_status(True)

                printout(">>>>     Now forget VDIs in the SR and make sure they are introduced correctly")            
                for vdi in self.session.xenapi.SR.get_VDIs(self.sr_ref):
                    printout(">>>>>        Doing forget-scan-introduce test on "\
                        "VDI: %s" % self.session.xenapi.VDI.get_name_label(vdi))
                    self.test_forget_scan_introduce([self.session.xenapi.VDI.get_uuid(vdi)])
                    display_operation_status(True)

                printout(">>> 2. Metadata VDI test.")
                printout(">>>>     Enable database replication on the SR")
                self.session.xenapi.SR.enable_database_replication(self.sr_ref)
                display_operation_status(True)

                printout(">>>>     Fetch and store metadata VDI details")
                vdis = self.session.xenapi.SR.get_VDIs(self.sr_ref)
                for vdi in vdis:
                    if self.session.xenapi.VDI.get_type(vdi) == 'metadata':
                        original_params = self.populate_vdi_xapi_fields(vdi)
                        break
                display_operation_status(True)

                # detach the SR
                printout(">>>>     Detach the SR ")
                old_config = self.detach_destroy_sr()

                # forget the SR
                printout(">>>>     Forget the SR")
                self.session.xenapi.SR.forget(self.sr_ref)
                display_operation_status(True)

                # introduce the SR
                printout(">>>>     Introduce the SR")
                self.sr_ref = self.session.xenapi.SR.introduce(self.sr_uuid, \
                                'XenCertTestSR', '', 'lvmoiscsi', '', True, {})
                self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
                display_operation_status(True)

                # attach the SR
                printout(">>>>     Attach the SR ")
                for host,config in old_config.items():
                    pbd = self.create_pbd(self.sr_ref, config, host)
                    self.plug_pbd(pbd)
                display_operation_status(True)

                # scan the SR
                printout(">>>>     Scan the SR")
                self.session.xenapi.SR.scan(self.sr_ref)
                display_operation_status(True)

                # Compare new VDI params with the original params 
                printout(">>>>     Compare new VDI params with the original params")
                vdi = self.session.xenapi.VDI.get_by_uuid(original_params['uuid'])
                new_params = self.populate_vdi_xapi_fields(vdi)
                # obviously, the SR-refs will be different
                if new_params.has_key('SR'):
                    del new_params['SR']
                if new_params != original_params:
                    diff = ''
                    for key in new_params.keys():                
                        if new_params[key] != original_params[key]:
                            if type(new_params[key]) is dict:
                                # make sure all the original keys are present
                                # and they match, ignore new keys added.
                                if set(new_params[key].keys()) - \
                                    set(original_params[key].keys()) == set([]):
                                    for inner_key in new_params[key].keys():
                                        if new_params[key][inner_key] != original_params[key][inner_key]:
                                            diff += "%s: original=%s , new=%s " % \
                                                (key, new_params[key], original_params[key])
                                            break
                            else:
                                diff += "%s: original=%s , new=%s " % \
                                    (key, new_params[key], original_params[key])
                    if diff != '':
                        raise Exception("New VDI params do not match the original params. "\
                                "Difference: %s" % diff)

                printout(">>>  3. VDI present in XAPI, but missing from metadata.")
                printout(">>>>     Delete one of the VDIs from the metadata")
                self.delete_vdi_from_metadata(vdi_uuid2)
                display_operation_status(True)

                printout(">>>>     Also remove the VDI from the storage")
                self.remove_vdi_from_storage(vdi_uuid2)
                display_operation_status(True)

                # scan the SR
                printout(">>>>     Scan the SR ")
                self.session.xenapi.SR.scan(self.sr_ref)
                display_operation_status(True)

                printout(">>>>     Make sure the VDI is removed from XAPI")
                found = False
                try:
                    self.session.xenapi.VDI.get_by_uuid(vdi_uuid2)
                    found = True
                except:
                    pass

                if found:
                    raise Exception("VDI %s absent from storage not removed " \
                                    "from XAPI on scan." % vdi_uuid2)

                printout(">>>  4. Snapshot relationship tests")
                printout(">>>>     Take 3 snapshots of the VDI")
                vdi_ref1 = self.session.xenapi.VDI.get_by_uuid(vdi_uuid1)
                (result, snap_ref1) = self.snapshot_vdi(vdi_ref1)
                snap_uuid1 = result_judge(result, "Failed to snapshot VDI. Error: %s " % snap_ref1, self.session.xenapi.VDI.get_uuid(snap_ref1))

                (result, snap_ref2) = self.snapshot_vdi(vdi_ref1)
                snap_uuid2 = result_judge(result, "Failed to snapshot VDI. Error: %s " % snap_ref2,
                                          self.session.xenapi.VDI.get_uuid(snap_ref2))

                (result, snap_ref3) = self.snapshot_vdi(vdi_ref1)
                snap_uuid3 = result_judge(result, "Failed to snapshot VDI. Error: %s  " % snap_ref3,
                                          self.session.xenapi.VDI.get_uuid(snap_ref3))
                display_operation_status(True)

                printout(">>>>     Normal scan case")
                printout(">>>>>        Forget the VDI and all its snapshots")
                self.test_forget_scan_introduce([vdi_uuid1, snap_uuid1, snap_uuid2, snap_uuid3])
                display_operation_status(True)

                printout(">>>>     A subset of snapshot VDIs lost, with the source VDI")
                printout(">>>>>        Forget the VDI and a subset of its snapshots")
                self.test_forget_scan_introduce([vdi_uuid1, snap_uuid1, snap_uuid3])
                display_operation_status(True)

                printout(">>>>     A subset of snapshot VDIs lost, without the source VDI")
                printout(">>>>>        Forget a subset of the snapshots")
                self.test_forget_scan_introduce([snap_uuid1, snap_uuid2])
                display_operation_status(True)

                printout(">>>>     Only the source VDI lost ")
                printout(">>>>>        Forget just the source VDI")
                self.test_forget_scan_introduce([vdi_uuid1])
                display_operation_status(True)

            except Exception, e:
                printout("Exception testing metadata SR scan tests. Error: %s" % str(e))
                retval = False
                display_operation_status(False)

        finally:
            if self.sr_ref is not None:
                printout(">>>> Delete VDIs on the SR")
                self.delete_vdi()

                printout(">>>> Detach the SR")
                self.detach_sr()

                printout(">>>> Destroy the SR")
                self.destroy_sr(self.sr_ref)
                display_operation_status(True)

        return retval

    def not_equal_exception(self, value_a, value_b, exception):
        if value_a != value_b:
            raise Exception(exception)
        display_operation_status(True)

    def metadata_sr_update_tests(self):
        try:
            retval = True
            self.sr_ref = None
            vdi_ref1 = None
            vdi_ref2 = None
            vdi_ref3 = None
            name_label_format = "%s-new"
            try:
                result = True
                printout(">>   SR UPDATE")
                printout(">>>      Create a SR")
                (retval, self.sr_ref, device_config) = self.create()
                retval_judge(retval, "      SR creation failed.    ")
                self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
                display_operation_status(True)
                
                printout(">>>      Add 3 VDIs")
                (result, vdi_ref1) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_1")
                vdi_uuid1 = result_judge(result, "Failed to create VDI. Error: %s   " % vdi_ref1, self.session.xenapi.VDI.get_uuid(vdi_ref1))
                (result, vdi_ref2) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_2")
                result_judge(result, "Failed to create VDI. Error: %s    " % vdi_ref2)
                (result, vdi_ref3) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_3")
                result_judge(result, "Failed to create VDI. Error: %s    " % vdi_ref3)
                display_operation_status(True)
                
                printout(">>>      SR update tests")
                printout(">>>>         Update the SR name-label")
                orig_name_label = self.session.xenapi.SR.get_name_label(self.sr_ref)
                self.session.xenapi.SR.set_name_label(self.sr_ref, name_label_format % orig_name_label)
                display_operation_status(True)
                
                printout(">>>>         Make sure the name-label is updated in the metadata")
                self.not_equal_exception(self.get_metadata_rec()[0]['name_label'], name_label_format % orig_name_label,
                                         "SR name-label not updated in metadata.")
                    
                printout(">>>>         Update the SR name-description")
                orig_name_description = self.session.xenapi.SR.get_name_description(self.sr_ref)
                self.session.xenapi.SR.set_name_description(self.sr_ref, name_label_format % orig_name_description)
                display_operation_status(True)
                    
                printout(">>>>         Make sure the name-description is updated in the metadata")
                self.not_equal_exception(self.get_metadata_rec()[0]['name_description'], name_label_format % orig_name_description,
                                         "SR name-description not updated in metadata.")
                
                printout(">>>>         Update the SR name_label and name-description")
                self.session.xenapi.SR.set_name_label(self.sr_ref, orig_name_label)
                self.session.xenapi.SR.set_name_description(self.sr_ref, orig_name_description)
                display_operation_status(True)
                    
                printout(">>>>         Make sure the name-label and name-description is updated in the metadata")
                if self.get_metadata_rec()[0]['name_label'] != orig_name_label or \
                    self.get_metadata_rec()[0]['name_description'] != orig_name_description:
                    raise Exception("SR name-label or name-description not updated in metadata.")
                display_operation_status(True)
                
                printout(">>>      VDI update tests")
                printout(">>>>         Update the VDI name-label")
                orig_name_label = self.session.xenapi.VDI.get_name_label(vdi_ref1)
                self.session.xenapi.VDI.set_name_label(vdi_ref1, name_label_format % orig_name_label)
                display_operation_status(True)
                
                printout(">>>>         Make sure the name-label is updated in the metadata")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1, 'vdi_uuid': vdi_uuid1})[1][vdi_uuid1]
                self.not_equal_exception(vdi_info['name_label'], name_label_format % orig_name_label,
                                         "VDI name-label not updated in metadata.")
                    
                printout(">>>>         Update the VDI name-description")
                orig_name_description = self.session.xenapi.VDI.get_name_description(vdi_ref1)
                self.session.xenapi.VDI.set_name_description(vdi_ref1, name_label_format % orig_name_description)
                display_operation_status(True)
                    
                printout(">>>>         Make sure the name-description is updated in the metadata")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1, 'vdi_uuid': vdi_uuid1})[1][vdi_uuid1]
                self.not_equal_exception(vdi_info['name_description'], name_label_format % orig_name_description,
                                         "VDI name-description not updated in metadata.")
                
                printout(">>>>         Update the VDI name_label and name-description")
                self.session.xenapi.VDI.set_name_label(vdi_ref1, orig_name_label)
                self.session.xenapi.VDI.set_name_description(vdi_ref1, orig_name_description)
                display_operation_status(True)
                    
                printout(">>>>         Make sure the name-label and name-description is updated in the metadata")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1, 'vdi_uuid': vdi_uuid1})[1][vdi_uuid1]
                if vdi_info['name_label'] != orig_name_label or \
                    vdi_info['name_description'] != orig_name_description:
                    raise Exception("VDI name-label or name-description not updated in metadata.")
                display_operation_status(True)
                
                printout(">>>      SR update tests with SR detached")
                # detach the SR
                printout(">>>>         Detach the SR")
                self.detach_sr()
                
                printout(">>>>         Update the SR name-label")
                orig_name_label = self.session.xenapi.SR.get_name_label(self.sr_ref)
                self.session.xenapi.SR.set_name_label(self.sr_ref, name_label_format % orig_name_label)
                display_operation_status(True)
                
                # attach the SR
                printout(">>>>         Attach the SR")
                self.attach_sr()
                
                printout(">>>>         Make sure the name-label is updated in the metadata ")
                self.not_equal_exception(self.get_metadata_rec()[0]['name_label'], name_label_format % orig_name_label,
                                         "SR name-label not updated in metadata.")
                
                # detach the SR
                printout(">>>>         Detach the SR")
                self.detach_sr()
                    
                printout(">>>>         Update the SR name-description")
                orig_name_description = self.session.xenapi.SR.get_name_description(self.sr_ref)
                self.session.xenapi.SR.set_name_description(self.sr_ref, name_label_format % orig_name_description)
                display_operation_status(True)
                
                # attach the SR
                printout(">>>>         Attach the SR")
                self.attach_sr()
                
                printout(">>>>         Make sure the name-description is updated in the metadata ")
                self.not_equal_exception(self.get_metadata_rec()[0]['name_description'], name_label_format % orig_name_description,
                                         "SR name-description not updated in metadata.")
                
                # detach the SR
                printout(">>>>         Detach the SR ")
                self.detach_sr()
                
                printout(">>>>         Update the SR name_label and name-description")
                self.session.xenapi.SR.set_name_label(self.sr_ref, orig_name_label)
                self.session.xenapi.SR.set_name_description(self.sr_ref, orig_name_description)
                display_operation_status(True)
                
                # attach the SR
                printout(">>>>         Attach the SR ")
                self.attach_sr()
                    
                printout(">>>>         Make sure the name-label and name-description is updated in the metadata ")
                if self.get_metadata_rec()[0]['name_label'] != orig_name_label or \
                    self.get_metadata_rec()[0]['name_description'] != orig_name_description:
                    raise Exception("SR name-label or name-description not updated in metadata.")
                display_operation_status(True)
                
                # detach the SR
                printout(">>>>         Detach the SR ")
                self.detach_sr()
                
                printout(">>>      VDI update tests with SR detached")
                printout(">>>>         Update the VDI name-label")
                orig_name_label = self.session.xenapi.VDI.get_name_label(vdi_ref1)
                self.session.xenapi.VDI.set_name_label(vdi_ref1, name_label_format % orig_name_label)
                display_operation_status(True)
                
                # attach the SR
                printout(">>>>         Attach the SR ")
                self.attach_sr()
                
                printout(">>>>         Make sure the name-label is updated in the metadata ")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1, 'vdi_uuid': vdi_uuid1})[1][vdi_uuid1]
                self.not_equal_exception(vdi_info['name_label'], name_label_format % orig_name_label,
                                         "VDI name-label not updated in metadata.")
                    
                # detach the SR
                printout(">>>>         Detach the SR  ")
                self.detach_sr()
                
                printout(">>>>         Update the VDI name-description")
                orig_name_description = self.session.xenapi.VDI.get_name_description(vdi_ref1)
                self.session.xenapi.VDI.set_name_description(vdi_ref1, name_label_format % orig_name_description)
                display_operation_status(True)
                
                # attach the SR
                printout(">>>>         Attach the SR  ")
                self.attach_sr()
                    
                printout(">>>>         Make sure the name-description is updated in the metadata ")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1, 'vdi_uuid': vdi_uuid1})[1][vdi_uuid1]
                self.not_equal_exception(vdi_info['name_description'], name_label_format % orig_name_description,
                                         "VDI name-description not updated in metadata.")
                
                # detach the SR
                printout(">>>>         Detach the SR  ")
                self.detach_sr()
                
                printout(">>>>         Update the VDI name_label and name-description")
                self.session.xenapi.VDI.set_name_label(vdi_ref1, orig_name_label)
                self.session.xenapi.VDI.set_name_description(vdi_ref1, orig_name_description)
                display_operation_status(True)
                
                # attach the SR
                printout(">>>>         Attach the SR  ")
                self.attach_sr()
                    
                printout(">>>>         Make sure the name-label and name-description is updated in the metadata ")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1, 'vdi_uuid': vdi_uuid1})[1][vdi_uuid1]
                if vdi_info['name_label'] != orig_name_label or \
                    vdi_info['name_description'] != orig_name_description:
                    raise Exception("VDI name-label or name-description not updated in metadata.")
                display_operation_status(True)
                
            except Exception, e:
                printout("Exception testing metadata SR update tests. Error: %s" % str(e))
                retval = False
                display_operation_status(False)
        
        finally:
            if self.sr_ref is not None:
                printout(">>>> Delete VDIs on the SR")
                self.delete_vdi()
                
                printout(">>>> Detach the SR")
                self.detach_sr()
                
                printout(">>>> Destroy the SR")
                self.destroy_sr(self.sr_ref)
                display_operation_status(True)
        
        return retval

    # vdi_list - list of vdi UUIDs
    def test_forget_scan_introduce(self, vdi_list):
        original_params = {}
        new_params = {}
        
        for vdi_uuid in vdi_list:
            # save params for the VDI
            original_params[vdi_uuid] = self.populate_vdi_xapi_fields(self.session.xenapi.VDI.get_by_uuid(vdi_uuid))
            if original_params[vdi_uuid]['is_a_snapshot']:
                original_params[vdi_uuid]['snapshot_of'] = self.session.xenapi.VDI.get_uuid(original_params[vdi_uuid]['snapshot_of'])
            if len(original_params[vdi_uuid]['snapshots']) > 0:
                snapshots_list = []
                for snapshot in original_params[vdi_uuid]['snapshots']:
                    snapshots_list.append(self.session.xenapi.VDI.get_uuid(snapshot))
                original_params[vdi_uuid]['snapshots'] = snapshots_list                    
        display_operation_status(True)
        
        for vdi_uuid in vdi_list:
            # Now forget the VDI
            printout(">>>>         Forgetting VDI: %s" % vdi_uuid)
            self.session.xenapi.VDI.forget(self.session.xenapi.VDI.get_by_uuid(vdi_uuid))
        display_operation_status(True)
            
        # Scan the SR so the VDI is introduced from metadata
        printout(">>>>         Now scan the SR and Make sure all the VDIs come up correctly")
        self.session.xenapi.SR.scan(self.sr_ref)
        
        for vdi in original_params.keys():
            # get the new params
            new_params  = \
                self.populate_vdi_xapi_fields(self.session.xenapi.VDI.get_by_uuid(vdi))
            if new_params['is_a_snapshot']:
                new_params['snapshot_of'] = self.session.xenapi.VDI.get_uuid(new_params['snapshot_of'])
            if len(new_params['snapshots']) > 0:
                snapshots_list = []
                for snapshot in new_params['snapshots']:
                    snapshots_list.append(self.session.xenapi.VDI.get_uuid(snapshot))
                new_params['snapshots'] = snapshots_list
            if new_params != original_params[vdi]:
                diff = ''
                for key in new_params.keys():                
                    if new_params[key] != original_params[vdi][key]:
                        if type(new_params[key]) is dict:
                            if set(new_params[key].keys()) - \
                                set(original_params[vdi][key].keys()) == set([]):
                                for inner_key in new_params[key].keys():
                                    if new_params[key][inner_key] != original_params[vdi][key][inner_key]:
                                        diff += "%s: original=%s , new=%s  " % \
                                            (key, new_params[key], original_params[vdi][key])
                                        break
                        elif type(new_params[key]) is list:
                            if sorted(new_params[key]) != sorted(original_params[vdi][key]):
                                diff += "%s: original=%s , new=%s  " % \
                                            (key, new_params[key], original_params[vdi][key])
                        else:
                            diff += "%s: original=%s , new=%s   " % \
                                (key, new_params[key], original_params[vdi][key])
                if diff != '':
                    raise Exception("New VDI params do not match the original params. "\
                        "Difference: %s" % diff)
                
    def parse_probe_output(self, output):
        dom = minidom.parseString(output)
        objectlist = dom.getElementsByTagName('SR')[0]
        return metadata._walkXML(objectlist)
        
    def metadata_general_vm_tests(self):
        #try:
        #    retval = True
        #    self.sr_ref = None
        #    vm_ref = None
        #    try:
        #        printout(">>   GENERAL VM TESTS")
        #        printout(">>>      Create a SR")
        #        self.sr_ref = self.Create_SR()
        #        self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
        #        display_operation_status(True)
        #    
        #        printout(">>>      Create a VM on this SR")
        #        vm_ref = 
        #return retval

        return True

    def cleanup_test_objs(self, sr_ref, vdi_ref_list = [], vbd_ref_list = [] ):
        # do a best effort cleanup of these objects
        # destroy VBDs
        for vbd in vbd_ref_list:
            # ignore if VBD is not valid
            try:
                vbd_uuid = self.session.xenapi.VBD.get_uuid(vbd)
            except:
                continue
            
            try:                
                self.session.xenapi.VBD.destroy(vbd)
            except Exception, e:
                xencert_print("Failed to destroy VBD: %s. Error: %s" % (vbd_uuid, str(e)))
            
        # destroy VDIs
        for vdi in vdi_ref_list:
            # ignore if VDI is not valid
            try:
                vdi_uuid = self.session.xenapi.VDI.get_uuid(vdi)
            except:
                continue
            try:
                self.session.xenapi.VDI.destroy(vdi)
            except Exception, e:
                xencert_print("Failed to destroy VDI: %s. Error:%s" % (vdi_uuid, str(e)))
        
        # destroy SR
        try:
            # ignore if SR is not valid
            try:
                sr_uuid = self.session.xenapi.SR.get_uuid(sr_ref)
            except:
                return             
            self.destroy_sr(sr_ref)
        except Exception, e:
            xencert_print("Failed to destroy SR: %s. Error:%s" % (sr_uuid, str(e)))        
            
    def metadata_scalibility_tests(self):
        return True
    
    def metadata_atomicity_tests(self):
        return True
        
    def verify_vdis_in_metadata(self, mdpath, list_of_vdi_uuids):
        return True
    
    def compare_md_with_xapi(self, vdi_uuid, md_vdi_info, xapi_vdi_info):
        return True
    
    def remove_mgt_volume(self):
        return True
    
    def set_md_path(self):
        return
    
    def get_metadata_rec(self, params = {}):
        return ({}, {})
        
    def remove_vdi_from_storage(self, vdi_uuid):
        return
    
    def delete_vdi_from_metadata(self, vdi_uuid):
        return
    
    def probe_sr(self):
        return ''


class BlockStorageHandler(StorageHandler):

    def __init__(self, storage_handler):
        super(BlockStorageHandler, self).__init__(storage_handler)
        self.gfs2_handler = StorageHandlerGFS2(self)
        self.enabled_clustering = False
        self.scsi_device_path = '/sys/class/scsi_device/'

    def Gfs2Supported(self):
        pools = self.session.xenapi.pool.get_all_records()
        pool = [pools[k] for k in pools][0]
        if not 'restrict_corosync' in pool['restrictions'] or \
                self.storage_conf['storage_type'] == "fcoe":
            return False

        if pool['restrictions']['restrict_corosync'] == "true":
            raise Exception("You are required to run these tests on a licensed host, with either Citrix Hypervisor Enterprise "
                            "or Citrix Virtual Apps or Desktop entitlement. This is so that the GFS2 SR can "
                            "be tested. If you are a Citrix Partner then demo licenses can be acquired through "
                            "the Citrix Ready programme. See https://www.citrix.co.uk/partner-programs/citrix-ready/ "
                            "for more details.")

        if not self.session.xenapi.Cluster.get_all():
            # cluster not enabled
            printout("Enabling clustering")
            management_pifs = [ref for (ref,pif) in
                               self.session.xenapi.PIF.get_all_records().items()
                               if pif['management']]
            [self.session.xenapi.PIF.set_disallow_unplug(pif, True) for
             pif in management_pifs]

            management_network = self.session.xenapi.PIF.get_record(
                management_pifs[0])['network']
            self.cluster = self.session.xenapi.Cluster.pool_create(
                management_network, 'corosync')
            self.enabled_clustering = True

        return True

    def DisableClustering(self):
        """
        If we enabled clustering disable it afterwards
        """
        if self.enabled_clustering:
            printout("Test enabled clustering, disabling at end")
            self.session.xenapi.Cluster.pool_destroy(self.cluster)

    def control_path_stress_tests(self):
        (retval_control, check_points_control, total_check_points_control) = \
            super(BlockStorageHandler, self).control_path_stress_tests()

        try:
            if self.Gfs2Supported():
                printout("Performing GFS2 control path stress tests.")
                (retval_control_gfs2, checkpoints_control_gfs2,
                 total_checkpoints_control_gfs2) = \
                    self.gfs2_handler.control_path_stress_tests()

                check_points_control += checkpoints_control_gfs2
                total_check_points_control += total_checkpoints_control_gfs2
                retval_control &= retval_control_gfs2
        finally:
            self.DisableClustering()

        return retval_control, check_points_control, total_check_points_control


class StorageHandlerISCSI(BlockStorageHandler):

    def __init__(self, storage_conf):
        xencert_print("Reached StorageHandlerISCSI constructor")
        self.device_config = {}
        self.device_config['target'] = storage_conf['target']
        self.device_config['targetIQN'] = storage_conf['targetIQN']
        self.device_config['SCSIid'] = storage_conf['SCSIid']
        self.iqn = storage_conf['targetIQN']
        super(StorageHandlerISCSI, self).__init__(storage_conf)

        
    def get_metadata_rec(self, params = {}):
        xencert_print("get_metadata_rec Enter")
        self.sr_uuid =  self.session.xenapi.SR.get_uuid(self.sr_ref)
        self.set_md_path()
        (sr_info, vdi_info) = LVMMetadataHandler(self.mdpath).getMetadata(params)
        xencert_print("get_metadata_rec Exit")
        return (sr_info, vdi_info)
        
    def check_metadata_vdi(self, vdi_ref):
        xencert_print("check_metadata_vdi Enter")
        self.sr_ref = self.session.xenapi.VDI.get_SR(vdi_ref)
        vdi_uuid = self.session.xenapi.VDI.get_uuid(vdi_ref)
        vdi_info = self.get_metadata_rec({'indexByUuid': 1, 'vdi_uuid': vdi_uuid})[1]
        verify_fields = self.populate_vdi_xapi_fields(vdi_ref)
        self.compare_md_with_xapi(vdi_uuid, vdi_info[vdi_uuid], verify_fields)
        xencert_print("check_metadata_vdi Exit")
    
    def verify_vdis_in_metadata(self, path, list_of_vdi_uuids):
        # get all VDIs from path and compare with the passed in VDIs
        vdi_info = self.get_metadata_rec({'indexByUuid': 1})[1]
        xencert_print("md_vdi_info: %s" % vdi_info)
        for vdi_uuid in list_of_vdi_uuids:
            if not vdi_info.has_key(vdi_uuid):
                raise Exception("VDI %s missing from the metadata." % vdi_uuid)
            else:    
                self.compare_md_with_xapi(vdi_uuid, vdi_info[vdi_uuid],
                    self.populate_vdi_xapi_fields(\
                                self.session.xenapi.VDI.get_by_uuid(vdi_uuid)))
    
    def compare_md_with_xapi(self, vdi_uuid, md_vdi_info, xapi_vdi_info):
        # remove irrelevant fields
        if xapi_vdi_info['is_a_snapshot'] == False:
            del md_vdi_info['snapshot_of']
            del md_vdi_info['snapshot_time']
        
        if xapi_vdi_info['type'] != 'metadata':
            del md_vdi_info['metadata_of_pool']
            
        for key in md_vdi_info.keys():
            if not xapi_vdi_info.has_key(key):
                continue
            
            md_value = md_vdi_info[key]
            xapi_value = xapi_vdi_info[key]
            if key == 'vdi_type':
                xapi_value = xapi_vdi_info['sm_config'][key]
            
            if key == 'snapshot_of':
                xapi_value = self.session.xenapi.VDI.get_uuid(xapi_vdi_info[key])
            
            if type(xapi_value) is bool:
                xapi_value = int(xapi_value)
            
            if type(xapi_value) is int:
                md_value = int(md_value)
            
            if md_value != xapi_value:
                raise Exception("VDI:%s key:%s Metadata:%s <> Xapi:%s doesn't match"%(vdi_uuid, key, md_value, xapi_value))

    def set_md_path(self):
        # come up with the management volume name
        # add SR name_label
        self.mdpath = os.path.join(VG_LOCATION, VG_PREFIX + self.sr_uuid)
        self.mdpath = os.path.join(self.mdpath, MDVOLUME_NAME)
        
    def remove_mgt_volume(self):
        login = False
        try:
            try:
                # logon to the iscsi session so LVs come up
                iscsilib.login(self.storage_conf['target'], self.storage_conf['targetIQN'], '', '')
                login = True
                
                # Allow the LVs to appear
                time.sleep(5)

                # remove the MGT volume
                remove(self.mdpath)
            except Exception, e:
                raise Exception("Failed to remove the management volume, error: %s" % str(e))
        finally:
            if login:
                # logout of the iscsi session
                iscsilib.logout(self.storage_conf['target'], self.storage_conf['targetIQN'])
                
    def remove_vdi_from_storage(self, vdi_uuid):
        path = os.path.join(VG_LOCATION, VG_PREFIX + self.sr_uuid)
        path = os.path.join(path, 'VHD-%s' % vdi_uuid)
        remove(path)
        
    def metadata_sr_attach_tests(self):
        retval = StorageHandler.metadata_sr_attach_tests(self)
        try:
            self.sr_ref = None
            vdi_ref3 = None
            try:
                result = True
                printout(">>> 5. LVM-specific tests")
                printout(">>>>     VDI present in both storage and metadata but differs in type.")
                printout(">>>>>        Create a SR")
                (retval, self.sr_ref, device_config) = self.create()
                retval_judge(retval, "      SR creation failed.")
                self.sr_uuid = self.session.xenapi.SR.get_uuid(self.sr_ref)
                display_operation_status(True)

                printout(">>>>>        Add 3 VDIs")
                (result, vdi_ref1) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_1")
                result_judge(result, "Failed to create VDI.")

                (result, vdi_ref2) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_2")
                result_judge(result, "Failed to create VDI.")

                (result, vdi_ref3) = self.create_vdi(self.sr_ref, 4 * StorageHandlerUtil.MiB, "sr_attach_test_vdi_3")
                vdi_uuid3 = result_judge(result, "Failed to create VDI. ", self.session.xenapi.VDI.get_uuid(vdi_ref3))
                display_operation_status(True)

                # rename a VHD- logical volume to begin with LV-
                printout(">>>>>        rename a VHD- logical volume to begin with LV-")
                path = os.path.join(VG_LOCATION, VG_PREFIX + self.sr_uuid)
                path = os.path.join(path, 'VHD-%s' % vdi_uuid3)
                newpath = os.path.join(path, 'LV-%s' % vdi_uuid3)
                rename(path, newpath)
                display_operation_status(True)

                # detach the SR
                printout(">>>>>        Detach the SR")
                self.detach_sr()

                # attach the SR
                printout(">>>>>        Attach the SR")
                self.attach_sr()

                # the entries for the missing VDIs should be removed from the metadata and XAPI
                printout(">>>>>        Check that the VDI type is changed in metadata.")
                vdi_info = self.get_metadata_rec({'indexByUuid': 1})[1]
                if vdi_info[vdi_uuid3]['vdi_type'] != 'aio':
                    raise Exception(" The type change for VDI %s not updated " \
                        "in the metadata. Type in metadata: %s" % \
                        (vdi_uuid3, vdi_info[vdi_uuid3]['vdi_type']))
                display_operation_status(True)

                # The type should also be chnaged in XAPI after a scan
                printout(">>>>>        Scan the SR and check that the VDI type is changed in XAPI.")
                self.session.xenapi.SR.scan(self.sr_ref)
                type_in_xapi = \
                    self.session.xenapi.VDI.get_sm_config(vdi_ref3)['vdi_type']
                if type_in_xapi != 'aio':
                    raise Exception(" The type change for VDI %s not updated " \
                        "in XAPI. Type in XAPI: %s" % (vdi_uuid3, type))
                else:
                    display_operation_status(True)

            except Exception, e:
                printout("Exception testing metadata SR attach tests. Error: %s" % str(e))
                retval = False
                display_operation_status(False)

        finally:
            if self.sr_ref is not None:
                printout(">>>>>    Delete VDIs on the SR")
                self.delete_vdi()

                printout(">>>>>    Detach the SR")
                self.detach_sr()

                printout(">>>>>    Destroy the SR")
                self.destroy_sr(self.sr_ref)
                display_operation_status(True)

        return retval

    def delete_vdi_from_metadata(self, vdi_uuid):
        self.set_md_path()
        LVMMetadataHandler(self.mdpath).delete_vdi_from_metadata(vdi_uuid)
        
    def probe_sr(self):
        try:
            return self.session.xenapi.SR.probe(util.get_localhost_uuid(self.session), self.device_config, "lvmoiscsi", self.sm_config)
        except Exception, e:
            # exceptions are not OK
            xencert_print("Exception probing lvmoiscsi SR with device_config %s "\
                         "and sm_config %s, error: %s" % \
                         (self.device_config, self.sm_config, str(e)))
            return ''        

    def create(self, device_config = {}):
        retval = True
        sr_ref = None
        try:
            xencert_print("First use XAPI to get information for creating an SR. ")
            list_scsi_id = []
            (list_portal, list_scsi_id) = StorageHandlerUtil.get_list_portal_scsi_id_for_iqn(self.session, self.storage_conf['target'], self.iqn, self.storage_conf['chapuser'], self.storage_conf['chappasswd'])
            
            # Create an SR
            printout("      Creating the SR.")
            device_config['target'] = self.storage_conf['target']
            if len(self.iqn.split(',')) > 1:
                device_config['targetIQN'] = '*'
            else:
                device_config['targetIQN'] = self.iqn
            if self.storage_conf['chapuser'] is not None and self.storage_conf['chappasswd'] is not None:
                device_config['chapuser'] = self.storage_conf['chapuser']
                device_config['chappassword'] = self.storage_conf['chappasswd']
            # try to create an SR with one of the LUNs mapped, if all fails throw an exception
            for scsi_id in list_scsi_id:
                try:                    
                    device_config['SCSIid'] = scsi_id
                    device_config_tmp = get_config_with_hidden_password(device_config, self.storage_conf['storage_type'])
                    xencert_print("The SR create parameters are %s, %s  " % (util.get_localhost_uuid(self.session), device_config_tmp))
                    sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', 'lvmoiscsi', '',True, {})
                    xencert_print("Created the SR %s" % sr_ref)
                    display_operation_status(True)
                    break

                except Exception:
                    xencert_print("Could not perform SR control tests with device %s, trying other devices." % scsi_id)
                    continue
                    
            if sr_ref is None:
                display_operation_status(False)
                retval = False
        except Exception, e:
            printout("   - Failed to create SR. Exception: %s" % str(e))
            display_operation_status(False)
            raise Exception(str(e))
        
        return (retval, sr_ref, device_config)
        
    def GetPathStatus(self, device_config):
        # Query DM-multipath status, reporting a) Path checker b) Path Priority handler c) Number of paths d) distribution of active vs passive paths
        try:
            self.map_host_to_ip = StorageHandlerUtil._init_adapters()      
            xencert_print("The host id to IP map is: %s" % self.map_host_to_ip) 
            
            (retval, config_map) = StorageHandlerUtil.get_config(device_config['SCSIid'])
            retval_judge(retval, "   - Failed to get SCSI config information for SCSI Id: %s" % device_config['SCSIid'])

            xencert_print("The config map extracted from scsi_id %s is %s" % (device_config['SCSIid'], config_map))
            
            # Get path_checker and priority handler for this device.
            (retval, mpath_config) = StorageHandlerUtil.parse_config(config_map['ID_VENDOR'], config_map['ID_MODEL'])
            retval_judge(retval, "   - Failed to get multipathd config information for vendor: %s and product: %s" % (config_map['ID_VENDOR'], config_map['ID_MODEL']))
            xencert_print("The mpath config extracted from multipathd is %s" % mpath_config)

            printout(">> Multipathd enabled for %s, %s with the following config" % (config_map['ID_VENDOR'], config_map['ID_MODEL']))
            printout("   please confirm that these settings are optimal:")
            printout("     device {")
            for key in mpath_config:
                printout("             %s %s" % (key, mpath_config[key]))

            printout("     }")
 
            (retval, self.listPathConfig) = StorageHandlerUtil.get_path_status(device_config['SCSIid'])
            retval_judge(retval, "Failed to get path status information for SCSI Id: %s" % device_config['SCSIid'])
            xencert_print("The path status extracted from multipathd is %s" % self.listPathConfig)
            
            return True
        except Exception, e:
            device_config_tmp = get_config_with_hidden_password(device_config, self.storage_conf['storage_type'])
            printout("   - Failed to get path status for device_config: %s. Exception: %s" % (device_config_tmp, str(e)))
            return False            

    def DisplayPathStatus(self):
        printout("       %-15s %-15s %-25s %-15s" % ('IP address', 'hbtl','Path DM status','Path status')            )
        for item in self.listPathConfig:
            printout("       %-15s %-15s %-25s %-15s" % (StorageHandlerUtil.find_ip_address(self.map_host_to_ip, item[0]), item[0], item[1], item[2]))    # NOSONAR
            
    def RandomlyFailPaths(self):
        try:
            self.no_of_paths = random.randint(1, len(self.listPathConfig) -1)    # NOSONAR
            self.blockedpathinfo = ''
            self.paths = ''
            for item in self.listPathConfig: 
                ip = StorageHandlerUtil.find_ip_address(self.map_host_to_ip, item[0])
                self.paths += ip + ','
                       
            self.paths = self.paths.rstrip(',')
            (self.blockedpathinfo) = self.block_unblock_paths(True, self.storage_conf['pathHandlerUtil'], self.no_of_paths, self.paths)
            print_on_same_line(" -> Blocking %d paths (%s)\n" % (self.no_of_paths, self.blockedpathinfo))
            return True                    
        except Exception, e:
            raise Exception(e)
        
    def functional_tests(self):
        logoutlist = []
        retval = True
        checkpoint = 0
        total_checkpoints = 4
        time_for_io_tests_in_sec = 0
        wildcard = False

        try:
            # Take SR device-config parameters and initialise data path layer.        
            printout("INITIALIZING SCSI DATA PATH LAYER ")
            
            iqns = self.storage_conf['targetIQN'].split(',')
            if len(iqns) == 1 and iqns[0]=='*':
                wildcard = True
            list_portal_iqns = []
            for target in self.storage_conf['target'].split(','):
                try:
                    iscsi_map = iscsilib.discovery(target, BaseISCSI.DEFAULT_PORT, self.storage_conf['chapuser'], self.storage_conf['chappasswd'])                                        
                except Exception, e:
                    printout("Exception discovering iscsi target: %s, exception: %s" % (target, str(e)))
                    display_operation_status(False)
                    raise
            
                # Create a list of portal IQN combinations.                
                for record in iscsi_map:
                    for iqn in iqns:
                        if record[2] == iqn or wildcard:
                            try:
                                list_portal_iqns.index((record[0], record[2]))
                            except Exception:
                                list_portal_iqns.append((record[0], record[2]))
                                break
            
            display_operation_status(True)
            checkpoint += 1

            # Now traverse through this multimap and for each IQN
            # Connect to all available portals in turn and verify that
            printout("DISCOVERING ADVERTISED SESSION TARGETS")
            printout("   %-70s %-20s" % ('IQN', 'Session Target'))
            for (portal, iqn) in list_portal_iqns:
                printout("   %-70s %-20s" % (iqn, portal))
        
            display_operation_status(True)
            checkpoint += 1

            printout("REPORT LUNS EXPOSED")
            printout(">> This test logs on to all the advertised target and IQN combinations")
            printout("   and discovers the LUNs exposed by each including information")
            printout("   like the LUN ID, SCSI ID and the size of each LUN.")
            printout("   This test also verifies that all the sessions from the same IQN ")
            printout("   expose the same number of LUNs and the same LUNs.")
            printout("")
            # Create a map of the following format
            # SCSIid -> (portal, iqn, device) tuple list            
            scsi_to_tuple_map = {}
            # and one of the following format
            # iqn -> [SCSI IDS]
            # for each portal below, check if iqn is in the map
            # if yes check if the SCSI Ids match, else report error
            # if iqn not in map add iqn and list of SCSI IDs.
            iqn_to_scsi_list = {}
            first_portal = True
            for (portal, iqn) in list_portal_iqns:
                try:
                    scsilist = []
                    # Login to this IQN, portal combination
                    iscsilib.login(portal, iqn, self.storage_conf['chapuser'], self.storage_conf['chappasswd'])
                    xencert_print("Logged on to the target.")
                    logoutlist.append((portal,iqn))                        
                            
                    # Now test the target
                    iscsilib._checkTGT(portal)
                    xencert_print("Checked the target.")
                    lun_to_scsi = StorageHandlerUtil.get_lun_scsiid_devicename_mapping(iqn, portal)
                    if len(lun_to_scsi.keys()) == 0:
                        raise Exception("   - No LUNs found!")
                        
                    xencert_print("The portal %s and the iqn %s yielded the following LUNs on discovery:" % (portal, iqn))
                    map_device_to_hbtl = scsiutil.cacheSCSIidentifiers()
                    xencert_print("The map_device_to_hbtl is %s" % map_device_to_hbtl)
                          
                    if first_portal:
                        printout("     %-23s\t%-4s\t%-34s\t%-10s" % ('PORTAL', 'LUN', 'SCSI-ID', 'Size(MiB)'))
                        first_portal = False
                    for key in lun_to_scsi.keys():
                        # Find the hbtl for this lun
                        scsilist.append(lun_to_scsi[key][0])
                        hbtl = map_device_to_hbtl[lun_to_scsi[key][1]]
                        hbtl_id = hbtl[1] + ":" + hbtl[2] + ":" + hbtl[3] + ":" + hbtl[4]
                        filepath = self.scsi_device_path + hbtl_id + '/device/block/*/size'

                        # For clearwater version, the file path is device/block:*/size
                        filelist = glob.glob(filepath)
                        if not filelist:
                            filepath = self.scsi_device_path + hbtl_id + '/device/block:*/size'
                            filelist = glob.glob(filepath)

                        xencert_print("The filepath is: %s" % filepath)
                        xencert_print("The hbtl_id is %s. The filelist is: %s" % (hbtl_id, filelist))
                        sectors = util.get_single_entry(filelist[0])
                        size = int(sectors) * 512 / 1024 / 1024
                        printout("     %-23s\t%-4s\t%-34s\t%-10s" % (portal, key, lun_to_scsi[key][0], size))
                        time_for_io_tests_in_sec += StorageHandlerUtil.find_disk_data_test_estimate(lun_to_scsi[key][1], size)
                        if scsi_to_tuple_map.has_key(lun_to_scsi[key][0]):
                            scsi_to_tuple_map[lun_to_scsi[key][0]].append(( portal, iqn, lun_to_scsi[key][1], size))
                        else:
                            scsi_to_tuple_map[lun_to_scsi[key][0]] = [( portal, iqn, lun_to_scsi[key][1], size)]

                except Exception, e:
                    printout("     ERROR: No LUNs reported by portal %s for iqn %s. Exception: %s" % (portal, iqn, str(e)))
                    xencert_print("     ERROR: No LUNs reported by portal %s for iqn %s." % (portal, iqn))
                    raise Exception("     ERROR: No LUNs reported by portal %s for iqn %s." % (portal, iqn))
                
                if iqn_to_scsi_list.has_key(iqn):
                    xencert_print("Reference scsilist: %s, current scsilist: %s" % (iqn_to_scsi_list[iqn], scsilist))
                    if iqn_to_scsi_list[iqn].sort() != scsilist.sort():
                        raise Exception("     ERROR: LUNs reported by portal %s for iqn %s do not match LUNs reported by other portals of the same IQN." % (portal, iqn))
                else:
                    iqn_to_scsi_list[iqn] = scsilist
                        
            display_operation_status(True)
            checkpoint += 1

            printout("DISK IO TESTS")
            printout(">> This tests execute a disk IO test against each available LUN to verify ")
            printout("   that they are writeable and there is no apparent disk corruption.")
            printout("   the tests attempt to write to the LUN over each available path and")
            printout("   reports the number of writable paths to each LUN.")
            seconds = time_for_io_tests_in_sec
            minutes = 0
            hrs = 0
            xencert_print("Total estimated time for the disk IO tests in seconds: %d" % time_for_io_tests_in_sec)
            if time_for_io_tests_in_sec > 60:
                minutes = time_for_io_tests_in_sec/60
                seconds = int(time_for_io_tests_in_sec - (minutes * 60))
                if minutes > 60:
                    hrs = int(minutes/60)
                    minutes = int(minutes - (hrs * 60))
                
            printout("   START TIME: %s " % (time.asctime(time.localtime())))
            
            if hrs > 0:
                printout("   APPROXIMATE RUN TIME: %s hours, %s minutes, %s seconds." % (hrs, minutes, seconds))
            elif minutes > 0:
                printout("   APPROXIMATE RUN TIME: %s minutes, %s seconds." % (minutes, seconds))
            elif seconds > 0:
                printout("   APPROXIMATE RUN TIME: %s seconds." % seconds)
            
            printout("")
            first_portal = True
            for key in scsi_to_tuple_map.keys():                                
                try:                    
                    total_checkpoints += 1
                    printout("     - Testing LUN with SCSI ID %-30s" % key)
                    
                    path_no = 0
                    path_passed = 0
                    for tuple in scsi_to_tuple_map[key]:                        
                        # If this is a root device then skip IO tests for this device.
                        if os.path.realpath(util.getrootdev()) == tuple[2]:
                            printout("     -> Skipping IO tests on device %s, as it is the root device." % tuple[2])
                            continue
                        
                        path_no += 1
        
                        # Execute a disk IO test against each path to the LUN to verify that it is writeable
                        # and there is no apparent disk corruption
                        print_on_same_line("        Path num: %d. Device: %s" % (path_no, tuple[2]))
                        try:
                            # First write a small chunk on the device to make sure it works                    
                            xencert_print("First write a small chunk on the device %s to make sure it works." % tuple[2])
                            cmd = self.util_pread_cmd + [self.util_of_param % tuple[2], 'conv=nocreat']
                            util.pread(cmd)
                            
                            xencert_print("lun size: %d MB" % tuple[3])
                            StorageHandlerUtil.disk_data_test(tuple[2], StorageHandlerUtil.get_blocks_num(tuple[3]))

                            xencert_print("Device %s passed the disk IO test. " % tuple[2])
                            path_passed += 1
                            printout("")
                            display_operation_status(True)
                            
                        except Exception, e:  
                            printout("        Exception: %s" % str(e))
                            display_operation_status(False)
                            xencert_print("Device %s failed the disk IO test. Please check if the disk is writable." % tuple[2] )
                        
                    if path_passed == 0:
                        display_operation_status(False)
                        raise Exception("     - LUN with SCSI ID %-30s. Failed the IO test, none of the paths were writable." % key)                        
                    else:
                        printout("        SCSI ID: %s Total paths: %d. Writable paths: %d." % (key, len(scsi_to_tuple_map[key]), path_passed))
                        display_operation_status(True)
                        checkpoint += 1                            
                                
                except Exception, e:                    
                    raise Exception("   - Testing failed while testing devices with SCSI ID: %s." % key)
                
            printout("   END TIME: %s " % (time.asctime(time.localtime())))
            
            checkpoint += 1
        
        except Exception, e:
            printout("- Functional testing failed due to an exception.")
            printout("- Exception: %s"  % str(e))
            retval = False
            
         # Logout of all the sessions in the logout list
        for (portal,iqn) in logoutlist:
            try:
                xencert_print("Logging out of the session: %s, %s" % (portal, iqn))
                iscsilib.logout(portal, iqn) 
            except Exception, e:
                printout("- Logout failed for the combination %s, %s, but it may not have been logged on so ignore the failure." % (portal, iqn))
                printout("  Exception: %s " % str(e))
        xencert_print("Checkpoints: %d, total_checkpoints: %s  " % (checkpoint, total_checkpoints))
        xencert_print("Leaving StorageHandlerISCSI functional_tests")

        return (retval, checkpoint, total_checkpoints)
    
    def __del__(self):
        xencert_print("Reached StorageHandlerISCSI destructor")
        StorageHandler.__del__(self)


class StorageHandlerHBA(BlockStorageHandler):
    def __init__(self, storage_conf):
        xencert_print("Reached StorageHandlerHBA constructor")
        super(StorageHandlerHBA, self).__init__(storage_conf)
        self.sr_type = "lvmo" + self.storage_conf['storage_type']

    def create(self):
        device_config = {}
        retval = True
        sr_ref = None
        try:
            xencert_print("First use XAPI to get information for creating an SR. ")
            (retval, list_adapters, list_scsi_id) = StorageHandlerUtil.get_hba_information(self.session, self.storage_conf, sr_type=self.sr_type)
            retval_judge(retval, "   - Failed to get available HBA information on the host.")
            if len(list_scsi_id) == 0:                
                raise Exception("   - Failed to get available LUNs on the host.")
            avaiable_scsi_ids = set(list_scsi_id) & set(self.storage_conf['scsiIDs'].split(','))
            if not avaiable_scsi_ids:
                raise Exception("   - None of the specificied SCSI IDs are available. "
                                "Please confirm that the IDs you provided are valid and that the LUNs are not already in use")
            # Create an SR
            # try to create an SR with one of the LUNs mapped, if all fails throw an exception
            printout("      Creating the SR.")
            for scsi_id in avaiable_scsi_ids:
                try:
                    device_config['SCSIid'] = scsi_id
                    xencert_print("The SR create parameters are %s, %s" % (util.get_localhost_uuid(self.session), device_config))
                    sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', self.sr_type, '',False, {})
                    xencert_print("Created the SR %s using device_config %s" % (sr_ref, device_config))
                    display_operation_status(True)
                    break

                except Exception, e:
                    xencert_print("Could not perform SR control tests with device %s, trying other devices." % scsi_id)
                    continue

            if sr_ref is None:
                display_operation_status(False)
                retval = False
        except Exception, e:
            printout("   - Failed to create SR. Exception: %s" % str(e))
            display_operation_status(False)
            raise Exception(str(e))

        return (retval, sr_ref, device_config)

    def GetPathStatus(self, device_config):
        # Query DM-multipath status, reporting a) Path checker b) Path Priority handler c) Number of paths d) distribution of active vs passive paths
        try:            
            (retval, config_map) = StorageHandlerUtil.get_config(device_config['SCSIid'])
            retval_judge(retval, "   - Failed to get SCSI config information for SCSI Id: %s" % device_config['SCSIid'])

            xencert_print("The config map extracted from scsi_id %s is %s" % (device_config['SCSIid'], config_map))
            
            # Get path_checker and priority handler for this device.
            (retval, mpath_config) = StorageHandlerUtil.parse_config(config_map['ID_VENDOR'], config_map['ID_MODEL'])
            retval_judge(retval, "   - Failed to get multipathd config information for vendor: %s and product: %s" % (config_map['ID_VENDOR'], config_map['ID_MODEL']))
                
            xencert_print("The mpath config extracted from multipathd is %s" % mpath_config)

            printout(">> Multipathd enabled for %s, %s with the following config:" % (config_map['ID_VENDOR'], config_map['ID_MODEL']))
            printout("     device {")
            for key in mpath_config:
                printout("             %s %s" % (key, mpath_config[key]))

            printout("     }")
 
            (retval, self.listPathConfig) = StorageHandlerUtil.get_path_status(device_config['SCSIid'])
            retval_judge(retval, "Failed to get path status information for SCSI Id: %s" % device_config['SCSIid'])
            xencert_print("The path status extracted from multipathd is %s" % self.listPathConfig)
            
            return True
        except Exception, e:
            printout("   - Failed to get path status for device_config: %s. Exception: %s" % (device_config, str(e)))
            return False            

    def DisplayPathStatus(self):
        printout("       %-15s %-25s %-15s" % ('hbtl','Path DM status','Path status')            )
        for item in self.listPathConfig:
            printout("       %-15s %-25s %-15s" % (item[0], item[1], item[2]))
            
    def RandomlyFailPaths(self):
        try:
            self.blockedpathinfo = ''
            self.no_of_paths = 0
            self.noOfTotalPaths = 0
            script_return = self.block_unblock_paths(True, self.storage_conf['pathHandlerUtil'], self.no_of_paths, self.storage_conf['pathInfo'])
            blocked_and_full = script_return.split('::')[1]
            self.no_of_paths = int(blocked_and_full.split(',')[0])
            self.noOfTotalPaths = int(blocked_and_full.split(',')[1])
            xencert_print("No of paths which should fail is %s out of total %s" % \
                                            (self.no_of_paths, self.noOfTotalPaths))
            self.blockedpathinfo = script_return.split('::')[0]
            print_on_same_line(" -> Blocking paths (%s)\n" % hide_path_info_password(self.blockedpathinfo))
            return True
        except Exception, e:            
            raise Exception(e)

    def functional_tests(self):
        retval = True
        checkpoint = 0
        total_checkpoints = 4
        time_for_io_tests_in_sec = 0
        total_time_for_io_tests_in_sec = 0
        scsi_id_list = self.storage_conf['scsiIDs'].split(",")

        try:
            # 1. Report the FC Host Adapters detected and the status of each physical port
            # Run a probe on the host with type lvmohba, parse the xml output and extract the HBAs advertised
            printout("DISCOVERING AVAILABLE HARDWARE HBAS")
            (retval, list_maps, scsilist) = StorageHandlerUtil.get_hba_information(self.session, self.storage_conf, sr_type=self.sr_type)
            if not retval:
                raise Exception("   - Failed to get available HBA information on the host.")
            else:
                xencert_print("Got HBA information: %s and SCSI ID list: %s" % (list_maps, scsilist))
           
            if len(list_maps) == 0:                    
                     raise Exception("   - No hardware HBAs found!")

            checkpoint += 1
            first = True

            for map in list_maps:                
                if first:
                    for key in map.keys():
                        print_on_same_line("%-15s\t" % key)
                    print_on_same_line("\n")
                    first = False

                for key in map.keys(): 
                    print_on_same_line("%-15s\t" % map[key])
                print_on_same_line("\n")

            display_operation_status(True)
            checkpoint += 1 
                
            # 2. Report the number of LUNs and the disk geometry for verification by user
            # take each host id and look into /dev/disk/by-scsibus/*-<host-id>*
            # extract the SCSI ID from each such entries, make sure all have same
            # number of entries and the SCSI IDs are the same.
            # display SCSI IDs and luns for device for each host id. 
            printout("REPORT LUNS EXPOSED PER HOST")
            printout(">> This test discovers the LUNs exposed by each host id including information")
            printout("   like the hbtl, SCSI ID and the size of each LUN.")
            printout("   The test also ensures that all host ids ")
            printout("   expose the same number of LUNs and the same LUNs.")
            printout("")
            first = True
            host_id_to_lun_list = {}
            # map from SCSI id -> list of devices
            scsi_to_tuple_map = {}
            # Create a map of the format SCSIid -> [size, time]
            # this is used to store size of the disk and the calculated time it takes to perform disk IO tests
            scsi_info = {}
            for map in list_maps:
                try:
                    (rval, list_lun_info) = StorageHandlerUtil.get_lun_information(map['id'])
                    if not rval:                                                    
                        raise Exception("Failed to get LUN information for host id: %s" % map['id'])
                    else:
                        # If one of the devices (and probably the only device) on this HBA
                        # is the root dev, skip it. The number of devices exposed on this HBA
                        # will not match the devices exposed on other adapters
                        root_found = False
                        for lun in list_lun_info:
                            if lun['device'] == os.path.realpath(util.getrootdev()):
                                xencert_print("Skipping host id %s with root device %s " % (map['id'], lun['device']))
                                root_found = True
                                break
                        if root_found == True: 
                            continue

                        xencert_print("Got LUN information for host id %s as %s" % (map['id'], list_lun_info))
                        host_id_to_lun_list[map['id']] = list_lun_info

                    printout("     The luns discovered for host id %s: " % map['id'])
                    map_device_to_hbtl = scsiutil.cacheSCSIidentifiers()
                    xencert_print("The map_device_to_hbtl is %s" % map_device_to_hbtl)

                    if first or len(list_lun_info) > 0:
                        printout("     %-4s\t%-34s\t%-20s\t%-10s" % ('LUN', 'SCSI-ID', 'Device', 'Size(MiB)'))
                        first = False
                        ref_list_luns = list_lun_info
                    else:
                        # Compare with ref list to make sure the same LUNs have been exposed.
                        if len(list_lun_info) != len(ref_list_luns):                            
                            raise Exception("     - Different number of LUNs exposed by different host ids.")
                               
                        # Now compare each element of the list to make sure it matches the ref list
                        for lun in list_lun_info:
                            found = False
                            for ref_lun in ref_list_luns:
                                if ref_lun['id'] == lun['id'] and ref_lun['SCSIid'] == lun['SCSIid']:
                                    found = True
                                    break
                            if not found:
                                raise Exception("     - Different number of LUNs exposed by different host ids.")
                            else:
                                continue
                        checkpoint += 1
                                                    
                    for lun in list_lun_info:
                        # Find the hbtl for this lun
                        hbtl = map_device_to_hbtl[lun['device']]
                        hbtl_id = hbtl[1] + ":" + hbtl[2] + ":" + hbtl[3] + ":" + hbtl[4]
                        filepath = self.scsi_device_path + hbtl_id + '/device/block/*/size'

                        # For clearwater version, the file path is device/block:*/size
                        filelist = glob.glob(filepath)
                        if not filelist:
                            filepath = self.scsi_device_path + hbtl_id + '/device/block:*/size'
                            filelist = glob.glob(filepath)

                        xencert_print("The filepath is: %s" % filepath)
                        xencert_print("The hbtl_id is %s. The filelist is: %s" % (hbtl_id, filelist))
                        sectors = util.get_single_entry(filelist[0])
                        size = int(sectors) * 512 / 1024 / 1024
                        printout("     %-4s\t%-34s\t%-20s\t%-10s" % (lun['id'], lun['SCSIid'], lun['device'], size))

                        time_for_io_tests_in_sec = 0
                        # Estimate test for only specified lun
                        if lun['SCSIid'] in scsi_id_list:
                            time_for_io_tests_in_sec = StorageHandlerUtil.find_disk_data_test_estimate(lun['device'], size)
                        if scsi_to_tuple_map.has_key(lun['SCSIid']):
                            scsi_to_tuple_map[lun['SCSIid']].append((lun['device'], size))
                            scsi_info[lun['SCSIid']][0] += size
                            scsi_info[lun['SCSIid']][1] += time_for_io_tests_in_sec
                        else:
                            scsi_to_tuple_map[lun['SCSIid']] = [(lun['device'], size)]
                            scsi_info[lun['SCSIid']] = [size,time_for_io_tests_in_sec]
        

                except Exception, e:
                    printout("     EXCEPTION: No LUNs reported for host id %s." % map['id'])
                    continue
                display_operation_status(True)

            checkpoint += 1

            # 3. Execute a disk IO test against each LUN to verify that they are writeable and there is no apparent disk corruption            
            printout("DISK IO TESTS")
            printout(">> This tests execute a disk IO test against each available LUN to verify ")
            printout("   that they are writeable and there is no apparent disk corruption.")
            printout("   the tests attempt to write to the LUN over each available path and")
            printout("   reports the number of writable paths to each LUN.")

            scsi_ids_to_test = {}

            # Create a pruned list which conatins only those SCSIids to be tested
            for key,value in scsi_to_tuple_map.items():
                if key in scsi_id_list:
                    scsi_ids_to_test[key] = value
                    total_time_for_io_tests_in_sec += scsi_info[key][1]

            # Check if the entered list contains invalid SCSIid entries
            if len(scsi_ids_to_test) != len(scsi_id_list):
                raise Exception("One or more SCSI-ID that was entered is invalid")

            seconds = total_time_for_io_tests_in_sec
            minutes = 0
            hrs = 0
            xencert_print("Total estimated time for the disk IO tests in seconds: %d" % total_time_for_io_tests_in_sec)
            if total_time_for_io_tests_in_sec > 60:
                minutes = int(total_time_for_io_tests_in_sec/60)
                seconds = int(total_time_for_io_tests_in_sec - (minutes * 60))
                if minutes > 60:
                    hrs = int(minutes/60)
                    minutes = int(minutes - (hrs * 60))
                
            printout("   START TIME: %s " % (time.asctime(time.localtime())))
            if hrs > 0:
                printout("   APPROXIMATE RUN TIME: %s hours, %s minutes, %s seconds." % (hrs, minutes, seconds))
            elif minutes > 0:
                printout("   APPROXIMATE RUN TIME: %s minutes, %s seconds." % (minutes, seconds))
            elif seconds > 0:
                printout("   APPROXIMATE RUN TIME: %s seconds." % seconds)            
            
            printout("")            
            total_checkpoints += 1
            for key in scsi_ids_to_test.keys():
                try:
                    total_checkpoints += 1
                    printout("     - Testing LUN with SCSI ID %-30s" % key)

                    path_no = 0
                    path_passed = 0
                    for device,size in scsi_ids_to_test[key]:
                        # If this is a root device then skip IO tests for this device.
                        if os.path.realpath(util.getrootdev()) == device:
                            printout("     -> Skipping IO tests on device %s, as it is the root device." % device)
                            continue

                        path_no += 1
                        
                        # Execute a disk IO test against each path to the LUN to verify that it is writeable
                        # and there is no apparent disk corruption
                        print_on_same_line("        Path num: %d. Device: %s" % (path_no, device))
                        try:
                            # First write a small chunk on the device to make sure it works
                            xencert_print("First write a small chunk on the device %s to make sure it works." % device)
                            cmd = self.util_pread_cmd + [self.util_of_param % device, 'conv=nocreat']
                            util.pread(cmd)
                            
                            xencert_print("lun size: %d MB" % size)
                            StorageHandlerUtil.disk_data_test(device, StorageHandlerUtil.get_blocks_num(size))
                            
                            xencert_print("Device %s passed the disk IO test. " % device)
                            path_passed += 1
                            printout("")
                            display_operation_status(True)

                        except Exception, e:
                            printout("        Exception: %s" % str(e))
                            display_operation_status(False)
                            xencert_print("Device %s failed the disk IO test. Please check if the disk is writable." % device )
                    if path_passed == 0:
                        display_operation_status(False)
                        raise Exception("     - LUN with SCSI ID %-30s. Failed the IO test, none of the paths were writable." % key)                        
                    else:
                        printout("        SCSI ID: %s Total paths: %d. Writable paths: %d." % (key, len(scsi_ids_to_test[key]), path_passed))
                        display_operation_status(True)
                        checkpoint += 1

                except Exception, e:
                    raise Exception("   - Testing failed while testing devices with SCSI ID: %s." % key)

            printout("   END TIME: %s " % (time.asctime(time.localtime())))
            checkpoint += 1

        except Exception, e:
            printout("- Functional testing failed due to an exception.")
            printout("- Exception: %s"  % str(e))
            retval = False
            
        xencert_print("Checkpoints: %d, total_checkpoints: %s  " % (checkpoint, total_checkpoints))
        xencert_print("Leaving StorageHandlerHBA functional_tests")

        return (retval, checkpoint, total_checkpoints)

    def __del__(self):
        xencert_print("Reached StorageHandlerHBA destructor")
        StorageHandler.__del__(self)


class StorageHandlerNFS(StorageHandler):

    def __init__(self, storage_conf):
        xencert_print("Reached StorageHandlerNFS constructor")
        self.server = storage_conf['server']
        self.serverpath = storage_conf['serverpath']        
        StorageHandler.__init__(self, storage_conf)

    def getSupportedNFSVersions(self):
        valid_nfs_versions = ['3', '4']
        supported_versions = []
        try:
            ns = util.pread2([RPCINFO_BIN, "-t", "%s" % self.server, "nfs"])
            for l in ns.strip().split("\n"):
                if l.split()[3] in valid_nfs_versions:
                    supported_versions.append(l.split()[3])

            return supported_versions
        except:
            xencert_print("Unable to obtain list of supported NFS versions")
            raise

    def create(self, nfsv='3'):
        device_config = {}
        device_config['server'] = self.server
        device_config['serverpath'] = self.serverpath
        device_config['nfsversion'] = nfsv
        retval = True
        try:
            # Create an SR
            printout("      Creating the SR. ")
            # try to create an SR with one of the LUNs mapped, if all fails throw an exception
            xencert_print("The SR create parameters are %s, %s " % (util.get_localhost_uuid(self.session), device_config))
            sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', 'nfs', '',False, {})
            xencert_print("Created the SR %s using device_config %s" % (sr_ref, device_config))
            display_operation_status(True)
            
        except Exception, e:            
            display_operation_status(False)
            raise Exception(("   - Failed to create SR. Exception: %s " % str(e)))
                    
        if sr_ref is None:
            display_operation_status(False)
            retval = False        
        
        return (retval, sr_ref, device_config)
    
    def __del__(self):
        xencert_print("Reached StorageHandlerNFS destructor")
        StorageHandler.__del__(self)

    def try_display_exported_paths(self, checkpoints):
        try:
            cmd = [nfs.SHOWMOUNT_BIN, "--no-headers", "-e", self.storage_conf['server']]
            list = util.pread2(cmd).split('\n')
            if len(list) > 0:
                printout("   %-50s" % 'Exported Path')
            for val in list:
                if len(val.split()) > 0:
                    printout("   %-50s" % val.split()[0])
            display_operation_status(True)
            checkpoints += 1
        except Exception, e:
            printout("   - Failed to display exported paths for server: %s. Exception: %s" % (
            self.storage_conf['server'], str(e)))
            raise e
        return checkpoints

    def try_filesystem_io_tests(self, mountpoint, checkpoints):
        try:
            testdir = os.path.join(mountpoint, 'XenCertTestDir-%s' % commands.getoutput('uuidgen'))
            try:
                os.mkdir(testdir, 755)
            except Exception, e:
                raise Exception("Exception creating directory: %s" % str(e))
            test_dir_create = True
            testfile = os.path.join(testdir, 'XenCertTestFile-%s' % commands.getoutput('uuidgen'))
            cmd = self.util_pread_cmd + [self.util_of_param % testfile]
            (rc, stdout, stderr) = util.doexec(cmd, '')
            test_file_created = True
            if rc != 0:
                raise Exception(stderr)
            display_operation_status(True)
            checkpoints += 1
        except Exception, e:
            printout("   - Failed to perform filesystem IO tests.")
            raise e
        return (testdir, checkpoints, test_dir_create, test_file_created, testfile)

    def functional_tests(self):
        retval = True
        checkpoints = 0
        total_checkpoints = 0
        test_file_created = False
        test_dir_create = False
        mount_created = False

        mountpoint = '/mnt/XenCertTest-' + commands.getoutput('uuidgen') 
        nfs_versions = self.getSupportedNFSVersions()
        for nfsv in nfs_versions:
            printout("Using NFSVersion: %s" % nfsv)
            total_checkpoints += 5
            try:
                # 1. Display various exports from the server for verification by the user. 
                printout("DISCOVERING EXPORTS FROM THE SPECIFIED TARGET")
                printout(">> This test probes the specified NFS target and displays the ")
                printout(">> various paths exported for verification by the user. ")
                checkpoints = self.try_display_exported_paths(checkpoints)
                
                # 2. Verify NFS target by mounting as local directory
                printout("VERIFY NFS TARGET PARAMETERS")
                printout(">> This test attempts to mount the export path specified ")
                printout(">> as a local directory. ")
                try:
                    util.makedirs(mountpoint, 755)
                    nfs.soft_mount(mountpoint, self.storage_conf['server'], 
                                   self.storage_conf['serverpath'], 'tcp', 
                                   timeout=600, nfsversion=nfsv)
                    mount_created = True
                    display_operation_status(True)
                    checkpoints += 1
                except Exception, e:
                    raise Exception("   - Failed to mount path %s:%s to %s, error: %s" % (self.storage_conf['server'], self.storage_conf['serverpath'], mountpoint, str(e)))
            
                # 2. Create directory and execute Filesystem IO tests
                printout("CREATE DIRECTORY AND PERFORM FILESYSTEM IO TESTS.")
                printout(">> This test creates a directory on the locally mounted path above")
                printout(">> and performs some filesystem read write operations on the directory.")
                (testdir, checkpoints, test_dir_create, test_file_created, testfile) = self.try_filesystem_io_tests(mountpoint, checkpoints)

                # 3. Report Filesystem target space parameters for verification by user
                printout("REPORT FILESYSTEM TARGET SPACE PARAMETERS FOR VERIFICATION BY THE USER")
                try:
                    printout("  - %-20s: %s" % ('Total space', util.get_fs_size(testdir)))
                    printout("  - %-20s: %s" % ('Space utilization',util.get_fs_utilisation(testdir)))
                    display_operation_status(True)
                    checkpoints += 1
                except Exception, e:
                    printout("   - Failed to report filesystem space utilization parameters. " )
                    raise e 
            except Exception, e:
                printout("   - Functional testing failed with error: %s" % str(e))
                retval = False   

            # Now perform some cleanup here
            try:
                if test_file_created:
                    os.remove(testfile)
                if test_dir_create:
                    os.rmdir(testdir)
                if mount_created:
                    nfs.unmount(mountpoint, True)
                checkpoints += 1
            except Exception, e:
                printout("   - Failed to cleanup after NFS functional tests, please delete the following manually: %s, %s, %s. Exception: %s" % (testfile, testdir, mountpoint, str(e)))

        return (retval, checkpoints, total_checkpoints)

    def control_path_stress_tests(self):
        sr_ref = None 
        retval = True
        checkpoint = 0
        total_checkpoints = 0
        pbd_plug_unplug_count = 10

        nfs_versions = self.getSupportedNFSVersions()
        for nfsv in nfs_versions:
            printout("Using NFSVersion: %s" % nfsv)
            total_checkpoints += 5
            try:
                printout("SR CREATION, PBD PLUG-UNPLUG AND SR DELETION TESTS")
                printout(">> These tests verify the control path by creating an SR, unplugging")
                printout("   and plugging the PBDs and destroying the SR in multiple iterations.")
                printout("")
            
                for i in range(0, 10):
                    printout("   -> Iteration number: %d" % i)
                    total_checkpoints += (2 + pbd_plug_unplug_count)
                    (retval, sr_ref, device_config) = self.create(nfsv)
                    checkpoint = retval_judge(retval, "      SR creation failed.    ", checkpoint, 1)
                
                    # Plug and unplug the PBD over multiple iterations
                    checkpoint += StorageHandlerUtil.plug_and_unplug_pbds(self.session, sr_ref, pbd_plug_unplug_count)
                    
                    # destroy the SR
                    printout("      Destroy the SR.  ")
                    StorageHandlerUtil.destroy_sr(self.session, sr_ref)
                    checkpoint += 1
                    
                printout("SR SPACE AVAILABILITY TEST")
                printout(">> This test verifies that all the free space advertised by an SR")
                printout("   is available and writable.")
                printout("")

                # Create and plug the SR and create a VDI of the maximum space available. Plug the VDI into Dom0 and write data across the whole virtual disk.
                printout("   Create a new SR.")
                try:
                    (retval, sr_ref, device_config) = self.create(nfsv)
                    checkpoint = retval_judge(retval, "      SR creation failed.     ", checkpoint, 1)

                    xencert_print("Created the SR %s using device_config: %s" % (sr_ref, device_config))
                    display_operation_status(True)
                except Exception, e:
                    display_operation_status(False)
                    raise e

                (check_point_delta, retval) = StorageHandlerUtil.perform_sr_control_path_tests(self.session, sr_ref)
                checkpoint = retval_judge(retval, "perform_sr_control_path_tests failed. Please check the logs for details.", checkpoint, check_point_delta)

            except Exception, e: 
                printout("- Control tests failed with an exception.")
                printout("  Exception: %s " % str(e))
                display_operation_status(False)
                retval = False

            try:
                # Try cleaning up here
                if sr_ref is not None:
                    StorageHandlerUtil.destroy_sr(self.session, sr_ref)
                    checkpoint += 1
            except Exception, e:
                printout("- Could not cleanup the objects created during testing, please destroy the SR manually. Exception: %s" % str(e))
                display_operation_status(False)

            xencert_print("Checkpoints: %d, total_checkpoints: %s   " % (checkpoint, total_checkpoints))
        
        return (retval, checkpoint, total_checkpoints)

    def mp_config_verification_tests(self):
        printout("MultiPathTests not applicable to NFS SR type.")
        return (True, 1, 1)

    def pool_tests(self):
        printout("pool_tests not applicable to NFS SR type.")
        return (True, 1, 1)


class StorageHandlerCIFS(StorageHandler):

    def __init__(self, storage_conf):
        xencert_print("Reached StorageHandlerCIFS constructor")
        self.server = storage_conf['server']
        self.username = storage_conf['username']
        self.password = storage_conf['password']
        StorageHandler.__init__(self, storage_conf)

    def create(self):
        device_config = {}
        device_config['server'] = self.server
        device_config['username'] = self.username
        device_config['password'] = self.password
        retval = True
        try:
            # Create an SR on the CIFS server/share provided.
            printout("      Creating the SR. ")
            device_config_tmp = get_config_with_hidden_password(device_config, self.storage_conf['storage_type'])
            xencert_print("The SR create parameters are %s, %s " % (util.get_localhost_uuid(self.session), device_config_tmp))
            sr_ref = self.session.xenapi.SR.create(util.get_localhost_uuid(self.session), device_config, '0', 'XenCertTestSR', '', 'cifs', '',False, {})
            xencert_print("Created the SR %s" % sr_ref)
            display_operation_status(True)

        except Exception, e:
            display_operation_status(False)
            raise Exception(("   - Failed to create SR. Exception: %s " % str(e)))

        if sr_ref is None:
            display_operation_status(False)
            retval = False

        return (retval, sr_ref, device_config)

    def __del__(self):
        xencert_print("Reached StorageHandlerCIFS destructor")
        StorageHandler.__del__(self)


    def pool_tests(self):
        printout("pool_tests not applicable to CIFS SR type.")
        return (True, 1, 1)

    def mp_config_verification_tests(self):
        printout("MultiPathTests not applicable to CIFS SR type.")
        return (True, 1, 1)

    def functional_tests(self):
        retval = True
        sr_ref = None
        checkpoints = 0
        total_checkpoints = 0
        test_file_created = False
        test_dir_create = False
        test_sr_created = False

        total_checkpoints += 3
        try:
            # 1. Create directory and execute Filesystem IO tests
            printout("CREATE CIFS SR AND PERFORM FILESYSTEM IO TESTS.")
            printout(">> This test creates a CIFS SR and performs filesystem ")
            printout(">> read write operations on the mounted directory.")
            try:
                # Create and plug SR
                xencert_print( " Create CIFS SR.")
                (retval, sr_ref, _trash) = self.create()
                retval_judge(retval, "      SR creation failed.     ")
                test_sr_created = True
                testdir = "/var/run/sr-mount/%s/XenCertTestDir-%s" % (self.session.xenapi.SR.get_uuid(sr_ref), commands.getoutput('uuidgen'))

                try:
                    os.mkdir(testdir, 755)
                except Exception,e:
                    raise Exception("Exception creating directory: %s" % str(e))
                test_dir_create = True
                testfile = os.path.join(testdir, 'XenCertTestFile-%s' % commands.getoutput('uuidgen'))
                cmd = self.util_pread_cmd + [self.util_of_param % testfile]
                (rc, stdout, stderr) = util.doexec(cmd, '')
                test_file_created = True
                if rc != 0:
                    raise Exception(stderr)
                display_operation_status(True)
                checkpoints += 1
            except Exception, e:
                printout("   - Failed to perform filesystem IO tests.")
                raise e

            # 2. Report Filesystem target space parameters for verification by user
            printout("REPORT FILESYSTEM TARGET SPACE PARAMETERS FOR VERIFICATION BY THE USER")
            try:
                printout("  - %-20s: %s " % ('Total space', util.get_fs_size(testdir)))
                printout("  - %-20s: %s " % ('Space utilization',util.get_fs_utilisation(testdir)))
                display_operation_status(True)
                checkpoints += 1
            except Exception, e:
                printout("   - Failed to report filesystem space utilization parameters. " )
                raise e
        except Exception, e:
            printout("   - Functional testing failed with error: %s" % str(e))
            retval = False

        # Now perform some cleanup here
        if sr_ref:
            try:
                if test_file_created:
                    os.remove(testfile)
                if test_dir_create:
                    os.rmdir(testdir)
                if test_sr_created:
                    StorageHandlerUtil.destroy_sr(self.session, sr_ref)
                checkpoints += 1
            except Exception, e:
                printout("   - Failed to cleanup after CIFS functional tests, please delete the following manually: %s, %s, %s(sr). Exception: %s" % (testfile, testdir, self.session.xenapi.SR.get_uuid(sr_ref), str(e)))

        return (retval, checkpoints, total_checkpoints)

    def control_path_stress_tests(self):
        sr_ref = None
        retval = True
        checkpoint = 0
        total_checkpoints = 0
        pbd_plug_unplug_count = 10

        total_checkpoints += 5
        try:
            printout("SR CREATION, PBD PLUG-UNPLUG AND SR DELETION TESTS")
            printout(">> These tests verify the control path by creating an SR, unplugging")
            printout("   and plugging the PBDs and destroying the SR in multiple iterations.")
            printout("")

            for i in range(0, 10):
                printout("   -> Iteration number: %d" % i)
                total_checkpoints += (2 + pbd_plug_unplug_count)
                (retval, sr_ref, device_config) = self.create()
                checkpoint = retval_judge(retval, "      SR creation failed.      ", checkpoint, 1)

                # Plug and unplug the PBD over multiple iterations
                checkpoint += StorageHandlerUtil.plug_and_unplug_pbds(self.session, sr_ref, pbd_plug_unplug_count)

                # destroy the SR
                printout("      Destroy the SR.  ")
                StorageHandlerUtil.destroy_sr(self.session, sr_ref)
                checkpoint += 1

            # Create and plug the SR and create a VDI of the maximum space available. Plug the VDI into Dom0 and write data across the whole virtual disk.
            printout("   Create a new SR.")
            try:
                (retval, sr_ref, device_config) = self.create()
                checkpoint = retval_judge(retval, "      SR creation failed.      ", checkpoint, 1)

                device_config_tmp = get_config_with_hidden_password(device_config, self.storage_conf['storage_type'])
                xencert_print("Created the SR %s using device_config: %s" % (sr_ref, device_config_tmp))
                display_operation_status(True)
            except Exception, e:
                display_operation_status(False)
                raise e

            (check_point_delta, retval) = StorageHandlerUtil.perform_sr_control_path_tests(self.session, sr_ref)
            checkpoint = retval_judge(retval, "perform_sr_control_path_tests failed. Please check the logs for details.", checkpoint, check_point_delta)

        except Exception, e:
            printout("- Control tests failed with an exception.")
            printout("  Exception: %s  " % str(e))
            display_operation_status(False)
            retval = False

        try:
            # Try cleaning up here
            if sr_ref is not None:
                StorageHandlerUtil.destroy_sr(self.session, sr_ref)
                checkpoint += 1
        except Exception, e:
            printout("- Could not cleanup the objects created during testing, please destroy the SR manually. Exception: %s" % str(e))
            display_operation_status(False)

        xencert_print("Checkpoints: %d, total_checkpoints: %s   " % (checkpoint, total_checkpoints))

        return (retval, checkpoint, total_checkpoints)

    def data_integrity_tests(self):
        retval = None
        checkpoint = 0
        total_checkpoints = 11

        vm_uuid = StorageHandlerUtil._get_localhost_uuid()
        xencert_print("Got vm_uuid as %s" % vm_uuid)
        vm_ref = self.session.xenapi.VM.get_by_uuid(vm_uuid)
        sr_ref = None
        vdi_ref = None
        vbd_ref = None

        try:
            #1) Create SR
            (retval, sr_ref, dconf) = self.create()
            printout("Created SR")
            checkpoint += 1

            #2) Create 4GB VDI in SR
            # XXX: Make create_vdi() return vdi_ref on success and raise an
            #      exception on failure. It is 'poorly implemented' atm :)
            (retval, vdi_ref) = self.create_vdi(
                    sr_ref,
                    4 * StorageHandlerUtil.GiB)
            retval_judge(retval, 'Error in VDI creation: %s' % vdi_ref)
            printout("Created 4GB VDI")
            checkpoint += 1

            #3) Attach VDI to dom0
            vbd_ref = StorageHandlerUtil.attach_vdi(self.session, vdi_ref,
                    vm_ref)
            printout("Attached the VDI to dom0")
            checkpoint += 1

            #4) Write known pattern to VDI
            StorageHandlerUtil.write_data_to_vdi(self.session, vbd_ref, 0, 3)
            printout("Wrote data to VDI")
            checkpoint += 1

            #5) Detach VDI
            StorageHandlerUtil.detach_vdi(self.session, vbd_ref)
            printout("Detached from dom0")
            checkpoint += 1

            #6) Resize VDI to 8GB
            self.resize_vdi(vdi_ref, 8 * StorageHandlerUtil.GiB)
            printout("Resized the VDI to 8GB")
            checkpoint += 1

            #7) Attach VDI to dom0
            vbd_ref = StorageHandlerUtil.attach_vdi(self.session, vdi_ref,
                    vm_ref)
            printout("VDI attached again to Dom0")
            checkpoint += 1

            #8) Write known pattern to second 4GB chunk
            StorageHandlerUtil.write_data_to_vdi(self.session, vbd_ref, 4, 7)
            printout("Wrote data onto grown portion of the VDI")
            checkpoint += 1

            #9) Detach VDI
            StorageHandlerUtil.detach_vdi(self.session, vbd_ref)
            printout("Detached from dom0")
            checkpoint += 1

            #10) Reattach VDI to dom0
            vbd_ref = StorageHandlerUtil.attach_vdi(self.session, vdi_ref,
                    vm_ref)
            printout("VDI attached again to Dom0")
            checkpoint += 1

            #11) Validate pattern on first and second 4GB chunks
            StorageHandlerUtil.verify_data_on_vdi(self.session, vbd_ref, 0, 7)
            printout("Verified data on complete VDI")
            checkpoint += 1

        except Exception as e:
            printout("Exception in CIFS Data Integrity tests: %s" % e)
            retval = False
        finally:
            # Cleanup here and return
            try:
                if checkpoint in {3, 4, 7, 8, 10, 11}:
                    StorageHandlerUtil.detach_vdi(self.session, vbd_ref)
                    printout('VDI successfully detached.')
                if checkpoint > 1:
                    self.destroy_vdi(vdi_ref)
                    printout('VDI successfully destroyed.')
                if checkpoint:
                    self.destroy_sr(sr_ref)
                    printout('SR successfully destroyed.')

                printout('Cleanup completed successfully.')

            except Exception as e:
                printout('Cleanup failed. Error: %s\nUser has to manually detach '
                      'and delete VDI with name-label "XenCertVDI-######" and '
                      'SR with name-label "XenCertTestSR"' % e)

        return (retval, checkpoint, total_checkpoints)

class StorageHandlerGFS2(StorageHandler):
    """
    Storage handler for GFS2 SRs
    """
    def __init__(self, base_handler):
        xencert_print("Reached StorageHandlerGFS2 constructor")

        self.base_handler = base_handler

        storage_conf = base_handler.storage_conf

        if isinstance(base_handler, StorageHandlerISCSI):
            provider = 'iscsi'
        elif isinstance(base_handler, StorageHandlerHBA):
            provider = 'hba'
        else:
            raise Exception('GFS does not support base %s' %
                            (base_handler.__class__.__name__))

        self.device_config = dict(provider=provider)

        if 'SCSIid' in storage_conf:
            # iSCSI provides a single SCSI id
            self.device_config['SCSIid'] = storage_conf['SCSIid']
        elif 'scsiIDs' in storage_conf and storage_conf['scsiIDs']:
            # HBA provides a list of SCSI ids, we only want one
            self.device_config['SCSIid'] = storage_conf['scsiIDs'].split(',')[0]

        # iSCSI also needs target, IQN
        if self.device_config['provider'] == 'iscsi':
            self.device_config['target'] = storage_conf['target']
            self.device_config['targetIQN'] = storage_conf['targetIQN']
            if storage_conf['chapuser'] is not None and storage_conf['chappasswd'] is not None:
                self.device_config['chapuser'] = storage_conf['chapuser']
                self.device_config['chappassword'] = storage_conf['chappasswd']

        super(StorageHandlerGFS2, self).__init__(storage_conf)

    def create(self):
        retval = True
        sr_ref = None

        try:
            xencert_print(
                "First use XAPI to get information for creating an SR.  ")

            if isinstance(self.base_handler, StorageHandlerISCSI):
                list_scsi_id = self.getIscsiScsiIds()
            elif isinstance(self.base_handler, StorageHandlerHBA):
                list_scsi_id = self.getHbaScsiIds()

            device_config = copy.deepcopy(self.device_config)

            if 'targetIQN' in device_config and len(device_config['targetIQN'].split(',')) > 1:
                device_config['targetIQN'] = '*'

            printout("      Creating the SR.  ")
            # try to create an SR with one of the LUNs mapped, if all fails
            # throw an exception
            for scsi_id in list_scsi_id:
                try:
                    device_config['SCSIid'] = scsi_id
                    device_config_tmp = get_config_with_hidden_password(device_config, self.storage_conf['storage_type'])
                    xencert_print("The SR create parameters are {}, {}".format(
                        util.get_localhost_uuid(self.session),
                        device_config_tmp))

                    sr_ref = self.session.xenapi.SR.create(
                            util.get_localhost_uuid(self.session),
                            device_config,
                            0,
                            "XenCertTestSR",
                            '',
                            'gfs2',
                            '',
                            True,
                            {}
                        )

                    xencert_print(
                        "Created the SR {} using device_config {}".format(
                            sr_ref, device_config_tmp))
                    display_operation_status(True)
                    break

                except Exception:
                    xencert_print(
                        "Could not perform SR control tests with device %s,"
                        " trying other devices." % scsi_id)

                if sr_ref is None:
                    display_operation_status(False)
                    retval = False
        except Exception as e:
            printout("    - Failed to create SR. Exception {}".format(str(e)))
            display_operation_status(False)
            raise

        return retval, sr_ref, device_config

    def getIscsiScsiIds(self):
        (list_portal, list_scsi_id) = \
            StorageHandlerUtil.get_list_portal_scsi_id_for_iqn(
                self.session, self.storage_conf['target'],
                self.storage_conf['targetIQN'],
                self.storage_conf['chapuser'],
                self.storage_conf['chappasswd'])
        return list_scsi_id

    def getHbaScsiIds(self):
        (retval, list_adapters, list_scsi_id) = StorageHandlerUtil.get_hba_information(self.session, self.storage_conf)
        retval_judge(retval, "   - Failed to get available HBA information on the host. ")
        if len(list_scsi_id) == 0:
            raise Exception("   - Failed to get available LUNs on the host.")
        avaiable_scsi_ids = set(list_scsi_id) & set(self.storage_conf['scsiIDs'].split(','))
        if not avaiable_scsi_ids:
            raise Exception("   - None of the specificied SCSI IDs are available."
                            " Please confirm that the IDs you provided are valid and that the LUNs are not already in use.")
        return list(avaiable_scsi_ids)


def get_storage_handler(g_storage_conf):
    # Factory method to instantiate the correct handler
    if g_storage_conf["storage_type"] == "iscsi":
        return StorageHandlerISCSI(g_storage_conf)

    if g_storage_conf["storage_type"] in ["hba", "fcoe"]:
        return StorageHandlerHBA(g_storage_conf)

    if g_storage_conf["storage_type"] == "nfs":
        return StorageHandlerNFS(g_storage_conf)

    if g_storage_conf["storage_type"] == "cifs":
        return StorageHandlerCIFS(g_storage_conf)

    return None
