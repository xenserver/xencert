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
import os
import re
import time
import glob
import random
import xml.dom.minidom
from XenCertLog import printout, print_on_same_line, xencert_print
from XenCertCommon import display_operation_status, get_config_with_hidden_password
import scsiutil
import util
import lvutil, vhdutil
from lvhdutil import MSIZE
import iscsilib
import mpath_cli
import mpath_dmp
import xs_errors


ISCSI_PROCNAME = "iscsi_tcp"
dev_path = '/dev/'
time_taken = '' 
bytesCopied = ''
speedOfCopy = ''
timeLimitControlInSec = 18000

MAX_TIMEOUT = 15

KiB = 1024
MiB = KiB * KiB
GiB = KiB * KiB * KiB

SECTOR_SIZE = 1 * GiB
CHAR_SEQ = "".join([chr(x) for x in range(256)])
CHAR_SEQ_REV = "".join([chr(x) for x in range(255, -1, -1)])
BUF_PATTERN = CHAR_SEQ + CHAR_SEQ
BUF_PATTERN_REV = CHAR_SEQ_REV + CHAR_SEQ_REV
BUF_ZEROS = "\0" * 512

DISKDATATEST = '/opt/xensource/debug/XenCert/diskdatatest'
DDT_SECTOR_SIZE = 512           # one sector size: 512 bytes
DDT_DEFAULT_BLOCK_SIZE = 512    # one block size: 512 sectors, 256KB

multiPathDefaultsMap = { 'udev_dir':'/dev',
			    'polling_interval':'5',
			    'selector': "round-robin 0",
			    'path_grouping_policy':'failover',
			    'getuid_callout':"/usr/lib/udev/scsi_id --whitelisted --replace-whitespace /dev/%n",
			    'prio_callout':'none',
			    'path_checker':'readsector0',
			    'rr_min_io':'1000',
			    'rr_weight':'uniform',
			    'failback':'manual',
			    'no_path_retry':'fail',
			    'user_friendly_names':'no',
			    'bindings_file':"/var/lib/multipath/bindings" }


def _init_adapters():
    # Generate a list of active adapters
    ids = scsiutil._genHostList(ISCSI_PROCNAME)
    util.SMlog("Host ids: %s" % ids)
    adapter = {}
    for host in ids:
        try:
            if hasattr(iscsilib, 'get_targetIP_and_port'):
                (addr, port) = iscsilib.get_targetIP_and_port(host)
            else:
                addr = util.get_single_entry(glob.glob(
                    '/sys/class/iscsi_host/host%s/device/session*/connection*/iscsi_connection*/persistent_address' % host)[0])
                port = util.get_single_entry(glob.glob(
                    '/sys/class/iscsi_host/host%s/device/session*/connection*/iscsi_connection*/persistent_port' % host)[0])
            adapter[host] = (addr, port)
        except Exception, e:
            xencert_print("Ignore host %d IP because of exception %s" % (host, str(e)))
    return adapter

def is_mp_enabled(session, host_ref):
    try:
        hconf = session.xenapi.host.get_other_config(host_ref)
        xencert_print("Host.other_config: %s" % hconf)
        
        if hconf['multipathing'] == 'true' and hconf['multipathhandle'] == 'dmp':
	    return True

    except Exception, e:
	xencert_print("Exception determining multipath status. Exception: %s" % str(e))
    return False

def enable_multipathing(session, host):
    try:
        session.xenapi.host.remove_from_other_config(host , 'multipathing')
        session.xenapi.host.remove_from_other_config(host, 'multipathhandle')
        session.xenapi.host.add_to_other_config(host, 'multipathing', 'true')
        session.xenapi.host.add_to_other_config(host, 'multipathhandle', 'dmp')

    except Exception, e:
	xencert_print("Exception enabling multipathing. Exception: %s" % str(e))

def disable_multipathing(session, host):
    try:
        session.xenapi.host.remove_from_other_config(host , 'multipathing')
        session.xenapi.host.remove_from_other_config(host, 'multipathhandle')
        session.xenapi.host.add_to_other_config(host, 'multipathing', 'false')

    except Exception, e:
	xencert_print("Exception disabling multipathing. Exception: %s" % str(e))

def block_ip(ip):
    try:
	cmd = ['iptables', '-A', 'INPUT', '-s', ip, '-j', 'DROP']
        util.pread(cmd)
    except Exception, e:
        xencert_print("There was an exception in blocking ip: %s. Exception: %s" % (ip, str(e)))

def unblock_ip(ip):
    try:
	cmd = ['iptables', '-D', 'INPUT', '-s', ip, '-j', 'DROP']
        util.pread(cmd)
    except Exception, e:
        xencert_print("There was an exception in unblocking ip: %s. Exception: %s" % (ip, str(e)))
   
def actual_sr_free_space(size):
    num = (size - lvutil.LVM_SIZE_INCREMENT - 4096 - vhdutil.calcOverheadEmpty(MSIZE)) * vhdutil.VHD_BLOCK_SIZE
    den = 4096 + vhdutil.VHD_BLOCK_SIZE

    return num/den

def get_config(scsiid):
    try:
	retval = True
	config_map = {}
	device = scsiutil._genReverseSCSIidmap(scsiid)[0]
	xencert_print("get_config - device: %s" % device)
	cmd = ["/usr/lib/udev/scsi_id", "--replace-whitespace", "--whitelisted", "--export", device]
	ret = util.pread2(cmd)
	xencert_print("get_config - scsi_if output: %s" % ret)
	for tuple in ret.split('\n'):
	    if tuple.find('=') != -1:
		config_map[tuple.split('=')[0]] = tuple.split('=')[1]

    except Exception, e:
	xencert_print("There was an exception getting SCSI device config. Exception: %s" % str(e))
	retval = False

    return (retval, config_map)

def find_ip_address(map_host_to_ip, hbtl):
    try:
        host = hbtl.split(':')[0]
        if host in map_host_to_ip and map_host_to_ip[host][0]:
            return map_host_to_ip[host][0]
    except Exception, e:
        xencert_print("Failed to find IP address for hbtl: %s, map_host_to_ip: %s, exception: %s" % (hbtl, map_host_to_ip, str(e)))
        raise Exception("No IP for hbtl %s in %s" % (hbtl, map_host_to_ip))

def getlist(xmlstr, tag):
    xmlstr = xmlstr.lstrip()
    xmlstr = xmlstr.lstrip('\'')
    xmlstr = xmlstr.rstrip()
    xmlstr = xmlstr.rstrip('\]')
    xmlstr = xmlstr.rstrip('\'')
    xmlstr = xmlstr.replace('\\n', '')
    xmlstr = xmlstr.replace('\\t', '')
    xencert_print("Got the probe xml as: %s" % xmlstr)
    dom = xml.dom.minidom.parseString(xmlstr)
    list = dom.getElementsByTagName(tag)
    return list

def tgt_childnodes_value(tgt):
	iqn = None
	portal = None
	for node in tgt.childNodes:
		if node.nodeName == 'TargetIQN':
			iqn = node.firstChild.nodeValue

		if node.nodeName == 'IPAddress':
			portal = node.firstChild.nodeValue
	return (iqn, portal)

def create_xml_string(items):
	xmlstr = ''
	for i in range(3, len(items)):
		xmlstr += items[i]
		xmlstr += ','
	return xmlstr

# The returned structure are a list of portals, and a list of SCSIIds for the specified IQN. 
def get_list_portal_scsi_id_for_iqn(session, server, target_iqn, chapuser  = None, chappassword = None):
    try:
	list_portal = []
	list_scsi_id= []
	device_config = {}
	device_config['target'] = server
	if chapuser  is not None and chappassword is not None:
	    device_config['chapuser'] = chapuser 
	    device_config['chappassword'] = chappassword

	try:
	    session.xenapi.SR.probe(util.get_localhost_uuid(session), device_config, 'lvmoiscsi')
	except Exception, e:
	    xencert_print("Got the probe data as: %s" % str(e))

	# Now extract the IQN list from this data.
	try:
	    # the target may not return any IQNs
	    # so prepare for it
	    items = str(e).split(',')
	    xmlstr = create_xml_string(items)

	    tgt_list = getlist(xmlstr.strip(','), "TGT")
	    for tgt in tgt_list:
		(iqn, portal) = tgt_childnodes_value(tgt)

		xencert_print("Got iqn: %s, portal: %s" % (iqn, portal))
		xencert_print("The target IQN is: %s" % target_iqn)
		if iqn == '*':
		    continue
		for targetiqn in target_iqn.split(','):
			if iqn == targetiqn:
				list_portal.append(portal)
				break

	    xencert_print("The portal list at the end of the iteration is: %s" % list_portal)
	except Exception, e:
	    raise Exception("The target %s did not return any IQNs on probe. Exception: %s" % (server, str(e)))

	#  Now probe again with each IQN in turn.
	for iqn in target_iqn.split(','):
	    try:
		device_config['targetIQN'] = iqn
		device_config_tmp = get_config_with_hidden_password(device_config, 'iscsi')
		xencert_print("Probing with device config: %s" % device_config_tmp)
		session.xenapi.SR.probe(util.get_localhost_uuid(session), device_config, 'lvmoiscsi')
	    except Exception, e:
		xencert_print("Got the probe data as: %s" % str(e))
    
	    # Now extract the SCSI ID list from this data.
	    try:
		# If there are no LUNs exposed, the probe data can be an empty xml
		# so be prepared for it
		items = str(e).split(',')
		xmlstr = create_xml_string(items)
		scsi_id_obj_list = getlist(xmlstr.strip(','), "SCSIid")
		for scsi_id_obj in scsi_id_obj_list:
			list_scsi_id.append(scsi_id_obj.firstChild.nodeValue)
			
	    except Exception, e:
		xencert_print("The IQN: %s did not return any SCSI IDs on probe. Exception: %s" % (iqn, str(e)))
		    
	    xencert_print("Got the SCSIId list for iqn %s as %s" % (iqn, list_scsi_id))
	    
	     
    except Exception, e: 
	xencert_print("There was an exception in get_list_portal_scsi_id_for_iqn. Exception: %s" % str(e))
	raise Exception(str(e))
	
    
    xencert_print("get_list_portal_scsi_id_for_iqn - returning PortalList: %s." % list_portal)  
    xencert_print("get_list_portal_scsi_id_for_iqn - returning SCSIIdList: %s." % list_scsi_id)  
    return (list_portal, list_scsi_id)

def extract_xml_from_exception(e):
    return ','.join(str(e).split(',')[3:])

def tgt_list_function(tgt_list, hba_filter, list):
	for tgt in tgt_list:
		map = {}
		for node in tgt.childNodes:
			map[node.nodeName] = node.firstChild.nodeValue
		if len(hba_filter) != 0:
			if hba_filter.has_key(map['host']):
				list.append(map)
		else:
			list.append(map)
	return list

def bd_list_function(bd_list, hba_filter, scsi_id_list):
	for bd in bd_list:
		for node in bd.childNodes:
			if node.nodeName == 'SCSIid':
				scsi_id = node.firstChild.nodeValue
			elif node.nodeName == 'adapter':
				adapter = ''.join(["host", node.firstChild.nodeValue])

		if len(hba_filter) != 0:
			if hba_filter.has_key(adapter):
				scsi_id_list.append(scsi_id)
		else:
			scsi_id_list.append(scsi_id)
	return scsi_id_list

# The returned structure are a list of portals, and a list of SCSIIds for the specified IQN. 
def get_hba_information(session, storage_conf, sr_type="lvmohba"):
    try:
	retval = True
	list = []
	scsi_id_list = []
	device_config = {}
	hba_filter = {}

	# Generate a map of the HBAs that the user want to test against.
	if storage_conf['adapters'] is not None:
	    for hba in storage_conf['adapters'].split(','):
			hba_filter[hba] = 1
	
	try:
	    session.xenapi.SR.probe(util.get_localhost_uuid(session), device_config, sr_type)
	except Exception, e:
	    xencert_print("Got the probe data as: %s " % str(e))
	    # Now extract the HBA information from this data.
	    try:
		# the target may not return any IQNs
		# so prepare for it
		xmlstr = extract_xml_from_exception(e)
		tgt_list = getlist(xmlstr, "Adapter")
		list = tgt_list_function(tgt_list, hba_filter, list)
		
		bd_list = getlist(xmlstr, "BlockDevice")
		scsi_id_list = bd_list_function(bd_list, hba_filter, scsi_id_list)
	
		xencert_print("The HBA information list being returned is: %s" % list)
	    except Exception, e:
		xencert_print("Failed to parse %s probe xml. Exception: %s" % (sr_type, str(e)))
	     
    except Exception, e: 
	xencert_print("There was an exception in get_hba_information: %s." % str(e))
	printout("Exception: %s" % str(e))
	retval = False
    
    xencert_print("get_hba_information - returning adapter list: %s and scsi id list: %s." % (list, scsi_id_list))  
    return (retval, list, scsi_id_list)

# the following details from the file name, put it into a list and return the list. 
def get_lun_information(id):
    retval = True
    list_lun_info = []
    try:
        # take in a host id, then list all files in /dev/disk/by_scsibus of the form *-5* then extract
        list = glob.glob('/dev/disk/by-scsibus/*-%s:*' % id)
        if len(list) == 0:
            retval = False
        else:
            for file in list:
                map = {}
                basename = os.path.basename(file)
                map['SCSIid'] = basename.split('-')[0]
                map['id'] = basename.split('-')[1].split(':')[3]
                map['device'] = os.path.realpath(file)
                list_lun_info.append(map)
    except Exception, e:
        printout("Failed to get lun information for host id: %s, error: %s" % (id, str(e)))
        retval = False

    return (retval, list_lun_info)
	    
def plug_and_unplug_pbds(session, sr_ref, count):
    print_on_same_line("      Unplugging and plugging PBDs over %d iterations. Iteration number: " % count)
    try:
	checkpoint = 0;
	for j in range(0, count):
	    print_on_same_line(str(j))
	    print_on_same_line('..')
	    pbds = session.xenapi.SR.get_PBDs(sr_ref)
	    xencert_print("Got the list of pbds for the sr %s as %s" % (sr_ref, pbds))
	    for pbd in pbds:
		xencert_print("Looking at PBD: %s" % pbd)
		session.xenapi.PBD.unplug(pbd)
		session.xenapi.PBD.plug(pbd)
	    checkpoint += 1

	print_on_same_line('\b\b  ')
	print_on_same_line('\n')
    except Exception, e:
	printout("     Exception: %s" % str(e))
	display_operation_status(False)
	
    display_operation_status(True)
    return checkpoint

def destroy_sr(session, sr_ref):	
    try:
	# First get the PBDs
	pbds = session.xenapi.SR.get_PBDs(sr_ref)
	xencert_print("Got the list of pbds for the sr %s as %s" % (sr_ref, pbds))
	xencert_print(" - Now unplug PBDs for the SR.")
	for pbd in pbds:
	    xencert_print("Unplugging PBD: %s" % pbd)
	    session.xenapi.PBD.unplug(pbd)	    

	xencert_print("Now destroying the SR: %s" % sr_ref)
	session.xenapi.SR.destroy(sr_ref)
	display_operation_status(True)
	
    except Exception, e:
	display_operation_status(False)
	raise Exception(str(e))
    
def create_max_size_vdi_and_vbd(session, sr_ref):
    vdi_ref = None
    vbd_ref = None
    retval = True
    vdi_size = 0
    
    try:
	try:
	    printout("   Create a VDI on the SR of the maximum available size.")
	    session.xenapi.SR.scan(sr_ref)
	    psize = session.xenapi.SR.get_physical_size(sr_ref)
	    putil = session.xenapi.SR.get_physical_utilisation(sr_ref)
	    vdi_size_act = actual_sr_free_space(int(psize) - int(putil))
	    vdi_size = str(min(1073741824, vdi_size_act)) # 1073741824 is by wkc hack (1GB)
	    xencert_print("Actual SR free space: %d, and used VDI size %s" % (vdi_size_act, vdi_size))

	    # Populate VDI args
	    args={}
	    args['name_label'] = 'XenCertTestVDI'
	    args['SR'] = sr_ref
	    args['name_description'] = ''
	    args['virtual_size'] = vdi_size
	    args['type'] = 'user'
	    args['sharable'] = False
	    args['read_only'] = False
	    args['other_config'] = {}
	    args['sm_config'] = {}
	    args['xenstore_data'] = {}
	    args['tags'] = []            
	    xencert_print("The VDI create parameters are %s" % args)
	    vdi_ref = session.xenapi.VDI.create(args)
	    xencert_print("Created new VDI %s" % vdi_ref)
	    display_operation_status(True)
	except Exception, e:	    
	    display_operation_status(False)
	    raise Exception(str(e))

	printout("   Create a VBD on this VDI and plug it into dom0")
	try:
	    vm_uuid = _get_localhost_uuid()
	    xencert_print("Got vm_uuid as %s" % vm_uuid)
	    vm_ref = session.xenapi.VM.get_by_uuid(vm_uuid)
	    xencert_print("Got vm_ref as %s" % vm_ref)

	
	    freedevs = session.xenapi.VM.get_allowed_VBD_devices(vm_ref)
	    xencert_print("Got free devs as %s" % freedevs)
	    if not len(freedevs):		
		raise Exception("No free devs found for VM: %s!" % vm_ref)
	    xencert_print("Allowed devs: %s (using %s)" % (freedevs, freedevs[0]))

	    # Populate VBD args
	    args={}
	    args['VM'] = vm_ref
	    args['VDI'] = vdi_ref
	    args['userdevice'] = freedevs[0]
	    args['bootable'] = False
	    args['mode'] = 'RW'
	    args['type'] = 'Disk'
	    args['unpluggable'] = True 
	    args['empty'] = False
	    args['other_config'] = {}
	    args['qos_algorithm_type'] = ''
	    args['qos_algorithm_params'] = {}
	    xencert_print("The VBD create parameters are %s" % args)
	    vbd_ref = session.xenapi.VBD.create(args)
	    xencert_print("Created new VBD %s" % vbd_ref)
	    session.xenapi.VBD.plug(vbd_ref)

	    display_operation_status(True)
	except Exception, e:
	    display_operation_status(False)
	    raise Exception(str(e))
    except Exception, e:
	printout("   Exception creating VDI and VBD, and plugging it into Dom-0 for SR: %s" % sr_ref)
	raise Exception(str(e))
    
    return (retval, vdi_ref, vbd_ref, vdi_size)

def attach_vdi(session, vdi_ref, vm_ref):
    vbd_ref = None

    try:
        printout("   Create a VBD on the VDI and plug it into VM requested")
        freedevs = session.xenapi.VM.get_allowed_VBD_devices(vm_ref)
        xencert_print("Got free devs as %s" % freedevs)
        if not len(freedevs):
            err_str = "No free devs found for VM: %s!" % vm_ref
            xencert_print(err_str)
            raise Exception(err_str)
        xencert_print("Allowed devs: %s (using %s)" % (freedevs, freedevs[0]))

        # Populate VBD args
        args = {}
        args['VM'] = vm_ref
        args['VDI'] = vdi_ref
        args['userdevice'] = freedevs[0]
        args['bootable'] = False
        args['mode'] = 'RW'
        args['type'] = 'Disk'
        args['unpluggable'] = True
        args['empty'] = False
        args['other_config'] = {}
        args['qos_algorithm_type'] = ''
        args['qos_algorithm_params'] = {}
        xencert_print("The VBD create parameters are %s" % args)

        vbd_ref = session.xenapi.VBD.create(args)
        session.xenapi.VBD.plug(vbd_ref)
        xencert_print("Created new VBD %s" % vbd_ref)

        return vbd_ref

    except Exception:
        printout("   Exception Creating VBD and plugging it into VM: %s" % vm_ref)
        raise

def detach_vdi(session, vbd_ref):
    try:
        session.xenapi.VBD.unplug(vbd_ref)
        xencert_print("Unplugged VBD %s" % vbd_ref)
        session.xenapi.VBD.destroy(vbd_ref)
        xencert_print("Destroyed VBD %s" % vbd_ref)
    except Exception as e:
        raise Exception('VDI detach failed. Error: %s' % e)

def find_time_to_write_data(devicename, size_in_mib):
    dd_out_file = 'of=' + devicename
    xencert_print("Now copy %dMiB data from /dev/zero to this device and record the time taken to copy it." % size_in_mib)
    cmd = ['dd', 'if=/dev/zero', dd_out_file, 'bs=4096', 'count=%d' % (size_in_mib * 256)]
    try:
	(rc, stdout, stderr) = util.doexec(cmd,'')
	list = stderr.split('\n')
	time_taken = list[2].split(',')[1]
	data_copy_time = int(float(time_taken.split()[0]))
	xencert_print("The IO test returned rc: %s stdout: %s, stderr: %s" % (rc, stdout, stderr))
	xencert_print("Time taken to copy %dMiB to the device %s is %d" % (size_in_mib, devicename, data_copy_time))
	return data_copy_time
    except Exception, e:
	raise Exception(str(e))

def perform_sr_control_path_tests(session, sr_ref):
    e = None
    try:
	checkpoint = 0
	vdi_ref = None
	vbd_ref = None
	retval = True

	(retval, vdi_ref, vbd_ref, vdi_size) = create_max_size_vdi_and_vbd(session, sr_ref)
	if not retval:
	    raise Exception("Failed to create max size VDI and VBD.")

	checkpoint += 2
	# Now try to zero out the entire disk
	printout("   Now attempt to write the maximum number of bytes on this newly plugged device.")

	devicename = dev_path + session.xenapi.VBD.get_device(vbd_ref)
	xencert_print("First finding out the time taken to write 1GB on the device.")
	time_for_512mib_sec = find_time_to_write_data(devicename, 512)
	time_to_write = int((float(vdi_size)/(1024*1024*1024)) * (time_for_512mib_sec * 2))

	if time_to_write > timeLimitControlInSec:
	    raise Exception("Writing through this device will take more than %s hours, please use a source upto %s GiB in size." %
			    (timeLimitControlInSec/3600, timeLimitControlInSec/(time_for_512mib_sec * 2)))
	minutes = 0
	hrs = 0
	if time_to_write > 60:
	    minutes = int(time_to_write/60)
	    time_to_write = int(time_to_write - (minutes * 60))
	    if minutes > 60:
		hrs = int(minutes/60)
		minutes = int(minutes - (hrs * 60))

	printout("   START TIME: %s " % (time.asctime(time.localtime())))

	if hrs > 0:
	    printout("   APPROXIMATE RUN TIME: %s hours, %s minutes, %s seconds." % (hrs, minutes, time_to_write))
	elif minutes > 0:
	    printout("   APPROXIMATE RUN TIME: %s minutes, %s seconds." % (minutes, time_to_write))
	elif time_to_write > 0:
	    printout("   APPROXIMATE RUN TIME: %s seconds." % (time_to_write))

	if not util.zeroOut(devicename, 1, int(vdi_size)):	    
	    raise Exception("   - Could not write through the allocated disk space on test disk, please check the log for the exception details.")
	    
	printout("   END TIME: %s " % (time.asctime(time.localtime())))
	display_operation_status(True)

	checkpoint += 1
	
    except Exception, e:
	printout("There was an exception performing control path stress tests. Exception: %s" % str(e))
	retval = False
    
    try:
	# Try cleaning up here
	if vbd_ref is not None:
	    session.xenapi.VBD.unplug(vbd_ref)
	    xencert_print("Unplugged VBD %s" % vbd_ref)
	    session.xenapi.VBD.destroy(vbd_ref)
	    xencert_print("Destroyed VBD %s" % vbd_ref)

	if vdi_ref is not None:
	    session.xenapi.VDI.destroy(vdi_ref)
	    xencert_print("Destroyed VDI %s" % vdi_ref)
    except Exception, e:
	printout("- Could not cleanup the objects created during testing, please destroy the vbd %s and vdi %s manually." % (vbd_ref, vdi_ref))
	printout("  Exception: %s" % str(e))
	
    return (checkpoint, retval)

def get_lun_scsiid_devicename_mapping(target_iqn, portal):
    iscsilib.refresh_luns(target_iqn, portal)
    lun_to_scsi_id={}
    path = os.path.join("/dev/iscsi",target_iqn,portal)
    try:
        for file in util.listdir(path):
            real_path = os.path.realpath(os.path.join(path, file))
            if file.find("LUN") == 0 and file.find("_") == -1:		
                lun=file.replace("LUN","")
                scsi_id = scsiutil.getSCSIid(os.path.join(path, file))
                lun_to_scsi_id[lun] = (scsi_id, real_path)

        return lun_to_scsi_id
    except util.CommandException:
        xencert_print("Failed to find any LUNs for IQN: %s and portal: %s" % (target_iqn, portal))
        return {}

def parse_multipathd_config(lines):
    """
    Convert multipathd config to dict
    :param lines: output lines of "/usr/sbin/multipathd show config", 
        for structure refer to https://linux.die.net/man/5/multipath.conf
    :return: a dict like:
        section: [
            (attribute, value),
            ...
            (subsection, [
                (attribute, value),
                ...
            ]),
            ...
        ],
        ...
    """
    dict = {}
    re_section_begin = re.compile(r'^([^\t ]+) {\n$')    # NOSONAR
    re_section_end = re.compile(r'^}\n$')
    re_section_attr = re.compile(r'^\t([^\t ]+) (.*[^{])\n$')    # NOSONAR
    re_subsection_begin = re.compile(r'^\t([^\t ]+) {\n$')    # NOSONAR
    re_subsection_end = re.compile(r'^\t}\n$')
    re_subsection_attr = re.compile(r'^\t\t([^\t ]+) (.*[^{])\n$')    # NOSONAR
    
    for line in lines:
        m = re_section_begin.match(line)
        if m:
            section = m.group(1)
            section_value = []
            continue
        m = re_section_attr.match(line)
        if m:
            attribute,value = m.group(1),m.group(2)
            section_value.append((attribute,value))
            continue
        m = re_subsection_begin.match(line)
        if m:
            subsection = m.group(1)
            subsection_value = []
            continue
        m = re_subsection_attr.match(line)
        if m:
            attribute,value = m.group(1),m.group(2)
            subsection_value.append((attribute,value))
            continue
        m = re_subsection_end.match(line)
        if m:
            section_value.append((subsection,subsection_value))
            continue
        m = re_section_end.match(line)
        if m:
            dict[section] = section_value
       # ignore any other line
       
    return dict

def parse_config(vendor, product):
    device_config = None
    try:
        cmd="show config"
        xencert_print("mpath cmd: %s" % cmd)
        (rc,stdout,stderr) = util.doexec(mpath_cli.mpathcmd,cmd)
        xencert_print("mpath output: %s" % stdout)
        d = parse_multipathd_config([line+'\n' for line in stdout.split('\n')])
        xencert_print("mpath config to dict: %s" % d)

        for _,device_value in d["devices"]:
            xencert_print("device attributes: %s" % device_value)
            attr_map = dict(device_value)
            if 'vendor' not in attr_map or 'product' not in attr_map:
                xencert_print("warning: skip the device attributes because can not find mandatory key vendor or product")
                continue
            re_vendor = re.compile(attr_map['vendor'].strip('"'))
            re_product = re.compile(attr_map['product'].strip('"'))
            if (re_vendor.search(vendor) and re_product.search(product)):
                xencert_print("matched vendor and product")
                device_config = dict(multiPathDefaultsMap.items() + attr_map.items())
                break
    except Exception, e:
        xencert_print("Failed to get multipath config for vendor: %s and product: %s. Exception: %s" % (vendor, product, str(e)))

    return (device_config != None, device_config)

def parse_xml_config(file):
    configuration = {}
    # predefines if not overriden in config file
    configuration['lunsize'] = '128'
    configuration['growsize'] = '4'

    config_info = xml.dom.minidom.parse(file)
    required = ['adapterid','ssid', 'spid', 'username', 'password', 'target']
    optional = ['port', 'protocol', 'chapuser', 'chappass', 'lunsize', 'growsize']
    for val in required + optional:
       try:
           configuration[val] = str(config_info.getElementsByTagName(val)[0].firstChild.nodeValue)
       except:
           if val in required:
               print("parse exception on REQUIRED ISL option: %s" % val)
               raise
           else:
               print("parse exception on OPTIONAL ISL option: %s" % val)
    return configuration

#Returns a list of following tuples for the SCSI Id given
#(hbtl, Path dm status, Path status) 
def get_path_status(scsi_id, only_active = False):
    list_paths = []
    list = []
    retval = True
    try:
        lines = mpath_cli.get_topology(scsi_id)
        list_paths = []
        for line in lines:
            m=mpath_cli.regex.search(line)
            if(m):
                list_paths.append(line)

        xencert_print("list_paths returned: %s" % list_paths)

        # Extract hbtl, dm and path status from the multipath topology output
        # e.g. "| |- 0:0:0:0 sda 8:0   active ready running"
        pat = re.compile(r'(\d+:\d+:\d+:\d+.*)$')    # NOSONAR

        for node in list_paths:
            xencert_print("Looking at node: %s" % node)
            match_res = pat.search(node)
            if match_res is None:
                continue

            # Extract path info if pattern matched successfully
            l = match_res.group(1).split()
            hbtl = l[0]
            dm_status = l[3]
            path_status = l[4]
            xencert_print("hbtl: %s" % hbtl)
            xencert_print("Path status: %s, %s" % (dm_status, path_status))

            if only_active:
                if dm_status == 'active':
                    list.append((hbtl, dm_status, path_status))
            else:
                list.append((hbtl, dm_status, path_status))

        xencert_print("Returning list: %s" % list)
    except Exception, e:
        xencert_print("There was some exception in getting path status for scsi id: %s. Exception: %s" % (scsi_id, str(e)))
        retval = False

    return (retval, list)

def _get_localhost_uuid():
    filename = '/etc/xensource-inventory'
    try:
        f = open(filename, 'r')
    except:
        raise xs_errors.XenError('EIO', \
              opterr="Unable to open inventory file [%s]" % filename)
    domid = ''
    for line in filter(util.match_domain_id, f.readlines()):
        domid = line.split("'")[1]
    return domid

def disk_data_test(device, test_blocks, sect_of_block=DDT_DEFAULT_BLOCK_SIZE, test_time=0):
    iter_start = str(random.randint(0, 100000))    # NOSONAR
    
    cmd = [DISKDATATEST, 'write', device, str(sect_of_block), str(test_blocks), str(test_time), iter_start]
    xencert_print("The command to be fired is: %s" % cmd)
    (rc, stdout, stderr) = util.doexec(cmd)
    if rc != 0:
        raise Exception("Disk test write error!")

    xencert_print("diskdatatest returned : %s" % stdout)
    last_string = stdout.strip().splitlines()[-1]
    total_blocks, write_blocks, write_elapsed, _ = last_string.split()
    total_blocks, write_blocks, write_elapsed = int(total_blocks), int(write_blocks), float(write_elapsed)

    cmd = [DISKDATATEST, 'verify', device, str(sect_of_block), str(write_blocks), str(test_time), iter_start]
    xencert_print("The command to be fired is: %s" % cmd)
    (rc, stdout, stderr) = util.doexec(cmd)
    if rc != 0:
        raise Exception("Disk test verify error!")

    xencert_print("diskdatatest returned : %s" % stdout)
    last_string = stdout.strip().splitlines()[-1]
    _, verify_blocks, verify_elapsed, sector_errors = last_string.split()
    verify_blocks, verify_elapsed, sector_errors = int(verify_blocks), float(verify_elapsed), int(sector_errors)

    if sector_errors != 0:
        raise Exception("Disk test verify error on %d sectors!", sector_errors)
        
    return total_blocks, write_blocks, write_elapsed, verify_blocks, verify_elapsed
    
def get_blocks_num(size, sect_of_block=DDT_DEFAULT_BLOCK_SIZE):
    return size*MiB/(sect_of_block*DDT_SECTOR_SIZE)
    
def find_disk_data_test_estimate(device, size):
    # Run diskdatatest in a report mode
    xencert_print("Run diskdatatest in a report mode with device %s to find the estimated time." % device)

    total_blocks, write_blocks, write_elapsed, verify_blocks, verify_elapsed = \
            disk_data_test(device, get_blocks_num(size), test_time=15)

    estimated_time = total_blocks * (write_elapsed/write_blocks + verify_elapsed/verify_blocks)
 
    xencert_print("Total estimated time for testing IO with the device %s as %d" % (device, estimated_time))
    return estimated_time

def _find_lun(svid):
    basepath = "/dev/disk/by-csldev/"
    if svid.startswith("NETAPP_"):
        # special attention for NETAPP SVIDs
        svid_parts = svid.split("__")
        globstr = basepath + "NETAPP__LUN__" + "*" + svid_parts[2] + "*" + svid_parts[-1] + "*"
    else:
        globstr = basepath + svid + "*"

    path = util.wait_for_path_multi(globstr, MAX_TIMEOUT)
    if not len(path):
        return []

    #Find CSLDEV paths
    svid_to_use = re.sub("-[0-9]*:[0-9]*:[0-9]*:[0-9]*$","",os.path.basename(path))    # NOSONAR
    devs = scsiutil._genReverseSCSIidmap(svid_to_use, pathname="csldev")

    #Find scsiID
    for dev in devs:
        try:
            scsi_id = scsiutil.getSCSIid(dev)
        except:
            pass

    #Find root device and return
    if not scsi_id:
        return []
    else:
        device=mpath_dmp.path(scsi_id)
        xencert_print("DEBUG: device path : %s" % (device))
        return [device]

def write_data_to_vdi(session, vbd_ref, start_sec, end_sec):
    xencert_print('write_data_to_vdi(vbd_ref=%s, start_sec=%s, end_sec=%s, ->Enter)' \
                 % (vbd_ref, start_sec, end_sec))
    try:
        device = os.path.join(dev_path, session.xenapi.VBD.get_device(vbd_ref))

        xencert_print('about to write onto device: %s' % device)

        with open(device, 'w+') as f:
            while start_sec <= end_sec:
                f.seek(start_sec * SECTOR_SIZE)
                f.write(BUF_PATTERN)
                start_sec += 1
    except Exception, e:
        raise Exception('Writing data into VDI:%s Failed. Error: %s' \
                % (vbd_ref, e))

    xencert_print('write_data_to_vdi() -> Exit')

def verify_data_on_vdi(session, vbd_ref, start_sec, end_sec):
    xencert_print('verify_data_on_vdi(vdi_ref=%s, start_sec=%s, end_sec=%s ->Enter)' \
                 % (vbd_ref, start_sec, end_sec))
    try:
        device = os.path.join(dev_path, session.xenapi.VBD.get_device(vbd_ref))

        xencert_print('about to read from device: %s' % device)

        expect = BUF_PATTERN

        with open(device, 'r+') as f:
            while start_sec <= end_sec:
                f.seek(start_sec * SECTOR_SIZE)
                actual = f.read(len(expect))
                if actual != expect:
                    raise Exception('expected:%s != actual:%s'\
                             % (expect, actual))
                start_sec += 1
    except Exception, e:
        raise Exception('Verification of data in VDI:%s Failed. Error:%s'\
                % (vbd_ref, e))

    xencert_print('verify_data_on_vdi() -> Exit')
