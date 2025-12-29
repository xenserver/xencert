<p><div class="content-wrapper"></p>  

# XenServer 9 Shared Storage Certification kit User Guide <!-- omit in toc -->

<br>

Published Jan 2026  
9.0.0 Edition


<br>

#### Table of Contents  
- [Overview](#overview)
- [Environmental Guidelines](#environmental-guidelines)
- [Installation](#installation)
- [Test Categories](#test-categories)
- [Shared storage certification kit usage explained](#shared-storage-certification-kit-usage-explained)
- [Execution time estimates](#execution-time-estimates)
- [Running shared storage certification kit against various storage types](#running-shared-storage-certification-kit-against-various-storage-types)
    - [Executing iSCSI tests](#executing-iscsi-tests)
    - [Executing HBA tests](#executing-hba-tests)
    - [Executing NFS tests](#executing-nfs-tests)
    - [Executing SMB tests](#executing-smb-tests)
    - [Executing Boot from SAN Multipath tests](#executing-boot-from-san-multipath-tests)
- [Forcing failure in multipath tests](#forcing-failure-in-multipath-tests)
    - [Failing paths with iSCSI storage](#failing-paths-with-iscsi-storage)
    - [Failing paths with HBA storage](#failing-paths-with-hba-storage)
- [Space Reclamation Tests](#space-reclamation-tests)
- [Log submission](#log-submission)
- [Appendix A-Blocking paths for failover testing](#appendix-a-blocking-paths-for-failover-testing)
    - [iSCSI storage type](#iscsi-storage-type)
    - [HBA storage type](#hba-storage-type)
- [Appendix B-Notes on storage discovery](#appendix-b-notes-on-storage-discovery)
- [Appendix C-Sample scripts provided with shared storage certification kit](#appendix-c-sample-scripts-provided-with-shared-storage-certification-kit)
    - [blockunblockpaths](#blockunblockpaths)
    - [blockunblockiscsipaths](#blockunblockiscsipaths)
    - [blockunblockhbapaths](#blockunblockhbapaths)
    - [blockunblockHBAport.sh.brocade](#blockunblockhbaportshbrocade)
    - [blockunblockHBAport.sh.qlogic](#blockunblockhbaportshqlogic)
    - [blockunblockHBAport.sh.cisco](#blockunblockhbaportshcisco)
- [Appendix D-Sample commands for testing multipathing with shared storage certification kit](#appendix-d-sample-commands-for-testing-multipathing-with-shared-storage-certification-kit)
    - [iSCSI](#iscsi)
    - [HBA](#hba)
      - [QLogic](#qlogic)
      - [Brocade](#brocade)
      - [Cisco](#cisco)

### Overview  

The purpose of this document is to familiarize the reader with XenServer Shared Storage Certification Kit. The certification kit is designed to certify the interoperability of various types of storage hardware with XenServer. The XenServer certification kit needs to be run with the latest version of the corresponding XenServer Release. Make sure that XenServer 9 has been updated to the latest version before testing.

Note that for Converged Network Adapters (CNAs) that provide iSCSI services, this certification suite will only verify the storage data path. The network functionality must be validated using the separate XenServer Hardware Test Kit.

### Environmental Guidelines  

- A pool of 2 or more hosts must be used for the tests, and they must be CLEAN installations of XenServer.  
- Please note that there must not be any additional IP addresses configured on the tested hosts, as this might break multipath failover testing for iSCSI storage targets.  
- **Important**: All LUNs corresponding to the transport type being tested will be used and ALL DATA ON THE ACCESSIBLE LUNS WILL BE ERASED. It is the responsibility of the administrator running the test to ensure that only the correct LUNs are visible or mapped to any of the pooled hosts. Also each of these LUNs should be writable for the functional testing to succeed.  
- All storage targets must be equally visible for all hosts in the test pool. The test will verify that for each target, the same LUNs or NFS shares are visible and accessible over each physical path. Asymmetric mapping of LUN paths will be flagged as a test failure.  
- All visible LUNs must be at least 1GB in size for test completion estimates to be reasonably accurate. An average of 10GB per LUN is recommended.  
- For multipathing failover tests at least 2 paths must be available.  
- If the storage target supports space reclamation (discard, unmap, trim), please enable the capability on the target before running the test-kit.  

### Installation
Shared storage certification kit is part of a separate supplemental pack. The pack needs to be installed after installing XenServer.

For the installation the supplemental pack xenserver-shared-storage-cert-kit-xs9.iso needs to be transferred to the control domain, Dom0, of the host under test using either wget or scp.  Copy the ISO onto the /root directory.

The supplemental pack subsequently needs to be installed using the following command:  

    xe update-upload file-name=”/root/xenserver-shared-storage-cert-kit-xs9.iso”  

The command returns the update uuid of shared storage certification kit package on successful upload.  

    xe update-apply uuid=<uuid of uploaded update> host=<host uuid>  

After installing the supplemental pack, shared storage certification kit can be found in the directory **/opt/xensource/debug/XenCert**. XenCert is made up of a number of scripts and support files:  

    XenCert
    XenCertCommon.py
    StorageHandler.py
    StorageHandlerUtil.py
    diskdatatest
    blockunblockpaths
    blockunblockiscsipaths
    blockunblockhbapaths-brocade
    blockunblockhbapaths-qlogic
    blockunblockhbapaths-cisco
    blockunblockHBAPort-brocade.sh
    blockunblockHBAPort-qlogic.sh
    blockunblockHBAPort-cisco.sh

### Test Categories  

The verification performed by the kit can be categorized into the following test types:  
 •  **Functional tests**: These tests initialize the data path layer and verify the control path and the test infrastructure configuration.  
•  **Control path stress tests**: These tests validate the Xen API control path for each storage type, issuing repetitive control path operations in succession.  
•  **Multipath configuration verification tests**: These tests verify that multipathing is configured correctly on the system, and the failover and restoration behavior comply with the supported standards.  
•  **Pool tests**: These tests ensure that the number of paths for a shared SR are consistent across various hosts in a pool.  
•  **Boot from SAN multipath tests (optional)**: These tests verify that boot from SAN multipath is setup properly, and the failover and restoration behavior complies with the supported standards.  
  
The certification tests validate a specific storage type and need to be run for all storage types separately to certify against all the types (lvmoisci, lvmohba and nfs).   

### Shared storage certification kit usage explained
Shared storage certification kit is controlled using the ./XenCert script. Its usage is described below:  

    ./XenCert –h

    Common options:
    -f functional	[optional] perform functional tests
    -c control      [optional] perform control path tests
    -m multipath	[optional] perform multipath configuration verification tests
    -o pool         [optional] perform pool verification tests
    -d data         [optional] perform data verification tests
    -M metadata		[optional] perform metadata tests
    -h help			[optional] show this help message and exit

    Storage specific options:

    Storage type iscsi:
    -t target		[required] comma separated list of Target names/IP addresses
    -q targetIQN	[required] comma separated list of target IQNs OR "*"
    -s SCSIid		[optional] SCSIid to use for SR creation
    -x chapuser		[optional] username for CHAP
    -w chappasswd	[optional] password for CHAP

    Storage type nfs:
    -n server		[required] server name/IP addr
    -e serverpath	[required] exported path

    Storage type cifs:
    -r server		[required] Full path to share root on CIFS server
    -y username		[required] The username to be used during CIFS authentication
    -p password		[required] The password to be used during CIFS authentication

    Storage type hba:
    -a adapters		[optional] comma separated list of HBAs to test against
    -S scsiIDs      [required] comma separated list of SCSI-IDs to test against

    Test specific options:
    Multipathing test options (-m above):
    -b storage_type	    [required] storage type (iscsi, hba, nfs, cifs)
    -u pathHandlerUtil	[optional] absolute path to admin provided callout utility which blocks/unblocks a list of paths, path related information should be provided with the -i option below
    -i pathInfo         [optional] pass-through string used to pass data to the callout utility above, for e.g. login credentials etc. This string is passed as-is to the callout utility.
    -g count		    [optional] count of iterations to perform in case of multipathing failover testing  

**Notes:**  

- The first 4 options in the list are flags for running specific tests rather than the whole suite. If none of the flags are specified ALL the tests mentioned above are run by the kit. 
- If the target IQNs are specified as “*” in the –q option above, ALL the LUNs accessible via the targets mentioned in –t will be accessed and ERASED. Please use the wildcard option with the utmost care.
- By default, there are 100 iterations of the multipath failover tests. This can be overridden by specifying a smaller value with the –g option above. This is particularly useful in case of manual failover like pulling out cables in case of Fibre Channel.  
  
### Execution time estimates 
Shared storage certification kit has been designed so as to limit the total execution time of the kit to 12 hours. This duration is partitioned between the various tests as:     
- **Functional tests:** Maximum 4 hours.   
- **Control path stress tests:** Maximum 6 hours.  
- **Multipath configuration verification tests:** N.A. as not IO intensive.  
- **Pool tests:** N.A. as not IO intensive.    

The tests will try to predict if it might take longer than the set limit and flag an error accordingly. Further, the maximum LUN size will also be indicated to help restrict the execution time to the respective limit.  

Please note however, that these estimates are arrived at run-time using some rough bandwidth estimation heuristics. Testers should allow for around 50% variance in the times indicated.  

**Important:** The 12 hours interval is a maximum execution time heuristic. If the execution completes earlier, the execution time should not be taken as a measure of correctness.  

### Running shared storage certification kit against various storage types  
##### Executing iSCSI tests
To be able to run the tests against an iSCSI target, the following details need to be specified:  

- The IP address(es) or Fully Qualified Domain Name(s) of one or more controllers. (Note that where controllers do not advertise the presence of other logically connected controllers during an iSCSI sendtargets query, you must explicitly enter the IP addresses/FQDNs of all controllers as a comma separated list)  
- List of IQN(s) of the target to be connected during the test. (Same as above, for controllers that do not advertise their peers, each target IQN must be explicitly added as a comma separated list). This is optional, as a wildcard “*” can be used if the exact IQNs are not known. However, this should be used with care, as explained in the usage section.  
- CHAP credentials for the targets, if required.  
**Note:**  CHAP authentication is not supported for GFS2 over iSCSI. Disable discovery and login authentication in your target, otherwise the control path stress tests will fail.
- If running multipathing tests, the script for the path failover simulation (blockunblockiscsipaths) needs to be specified. The script is described further in Appendix C for the unexpected case that it needs to be modified.  

The test can then be initiated using the following command: 

    #  ./XenCert -b iscsi -t <IP1,IP2,..> -q <IQN1,IQN2,…> -u <fullpath>/blockunblockiscsipaths  

The above command will run all 4 categories of tests. If required, specific flags can be used to run particular tests only. Control path stress tests will be executed using fully provisioned LVM storage mapping and thin provisioned Global Filesystem 2 (GFS2) distributed filesystem storage.  

Note that the IQNs visible to the XenServer host from a given IP address(es) can be probed using the XenServer CLI command outlined in the Appendix B.  

**Note:**  

To extend support for alternate multipath configurations:  

- Change multipath.conf as appropriate.  
- Rerun the multipath tests as outlined above.  
- Update the verification form fields (Note changes to multipath.conf and update Test: XC.MultiPath (alternate multipath configuration - optional)).  

##### Executing HBA tests
To be able to run these tests, the system will need to have access to LUNs from hardware HBAs. Thus before running the test the user will need access to:  

- A list of adapters to run the test against  
- If performing multipathing tests, a path block utility, which would take in some adapter specific information and block the paths. For sample scripts (blockunblockhbapaths) and parameters refer to Appendix C. There are various examples for different switch vendors included with shared storage certification kit.  
- The information required by the block and unblock utility to work. See example below.  

The test can then be initiated using the following command: 
```
    #  ./XenCert -b <hba> –a <adapter1,adapter2,…> -S <SCSIID1, SCSIID2,..> –u <full path >/blockunblockhbapaths –i fc-switch-IP:username:password:port-no-1,port-no-2  
```
If no adapter is specified, the tests would be run against all the adapters with LUNs mapped to the server where shared storage certification kit is being executed. The above command will run all 4 categories of tests. If required, specific flags can be used to run particular tests only. Control path stress tests will be executed using fully provisioned LVM storage mapping and thin provisioned Global Filesystem 2 (GFS2) distributed filesystem storage.  

Note that the adapters known to the XenServer host can be probed using the XenServer CLI command outlined in the Appendix B.  

**Important:** Please note that the ports specified using the –i option should be visible from the host. Specifying non-available ports may lead to pathological scenarios like blocking both the paths to a device, so additional care needs to be taken when specifying the pass-through information using -i. 

**Note:**  

To extend support for alternate multipath configurations:   

- Change multipath.conf as appropriate.  
- Rerun the multipath tests as outlined above.  
- Update the verification form fields (Note changes to multipath.conf and update Test: XC.MultiPath (alternate multipath configuration - optional)).  
 
##### Executing NFS tests
To be able to run the tests against a NFS target, the following details need to be specified:  

- The IP addresses or fully qualified DNS names of the NFS server
- The target path to be used within the server  

The test can then be initiated using the following command: 

    #  ./XenCert -b nfs –n <server> -e <serverpath>  

The above command will run all 4 categories of tests. If required, specific flags can be used to run particular tests only. The multipathing and pool tests are not valid for NFS and are not supported or tested. 

Note that the NFS target mount points visible to the XenServer host can be probed using the XenServer CLI command outlined in the Appendix B.

##### Executing SMB tests  

To be able to run the tests against a SMB share, the following details need to be specified:  

- Full path of the share root on the SMB server  
- Username and password to authenticate on the server  

The test can then be initiated using the following command:  

    ./XenCert <test_flags> -b cifs –r <share> -y <username> -p <password>  

For example if:  

- path_to_share: //192.168.100.100/smb_share_5  
- username: jane_doe    
- password: s3Cure_password  

To run the Functional and Data Integrity tests only, use the following command:  

    ./XenCert -f -d -b cifs -r //192.168.100.100/smb_share_5 -y jane_doe -p s3Cure_password  

If `<test_flags>` is empty, all the tests applying to SMB will run.  

Valid SMB tests with their respective flags are:  

- Functional:		-f  
- Control Path:		-c  
- Data Integrity:		-d  

##### Executing Boot from SAN Multipath tests  

Please follow the following steps below to perform boot from SAN multipath tests manually. For more information, see [boot from san](https://docs.xenserver.com/en-us/xenserver/9/install/advanced-install.html#boot-from-san).

1. Ensure that your array has boot from SAN capability  
2. Configure your array for multipath support (multiple paths to the XenServer)  
3. Install XenServer on the LUN provided by the SAN with multipathing enabled, as outlined in the Xenserver Installation Guide.  
4. Make sure that the expected number of paths are being used for the boot LUN.  
5. Unplug a single path/Fiber cable of the SAN and observe that the path has indeed not present on the XenServer within a maximum time of 50 seconds. (Fail over)  
6. Plug in the disconnected cable and look for the failed paths to be active again within a maximum time of 2 minutes.(Fail back)  
7. Repeat this step for all the available paths.  
8. Observe the number of paths available before and after the test and they should be consistent.  
**Note:**  Multipath boot from SAN is currently supported on hardware HBAs only. (SAS, HBA)  

### Forcing failure in multipath tests  
Multipath tests are intended to exercise the port failover capabilities within a single host.  Note that these tests only apply to the LVM over iSCSI and LVM over HBA (iscsi, hba) storage types.  
##### Failing paths with iSCSI storage  
For failing paths in the case of iSCSI storage, the nft command can be used. Sample commands for blocking and unblocking paths have been posted in the Appendix A. 
##### Failing paths with HBA storage   
There are several ways to fail paths in the case of hba storage:  

- Cable pull
- Fabric port disable (if applicable)
- Fabric switch disable/crash (if applicable)  

Sample bash scripts for blocking and unblocking paths by logging on to a qlogic SANbox switch have been pasted in the Appendix A.  


### Space Reclamation Tests
Targets which support space reclamation should be tested with this capability, so the functionality can be supported. 

To perform these tests, please follow the steps below:  

**Before running the test-kit:**  

- Login to the storage back-end and enable space reclamation capabilities (trim, unmap, discard).  
- Note LUN capacity under test and update verification form's Space Reclamation section.  

**After running the test-kit:**  

- Note used space on the LUN under test and update the verification form's Space Reclamation section.

### Log submission  

If you need to authenticate Storage, please download the verification form: <a href="xenserver-shared-storage-verification-form.docx" download="xenserver-shared-storage-verification-form.docx">xenserver-shared-storage-verification-form </a>  

There are a number of required items necessary for submission. These are:  

- Completely filled out shared storage certification kit Verification Results Form (one per storage type).  
- Every test run will usually create an additional log file which will mirror the output shown on the terminal while the test runs. The location of this file will be reported at the end of each test, these log files will need to be submitted with the form above as well. A sample result output with the report file name will look like:  
```
***********************************************************************
End of shared storage certification kit certification suite.
Please find the report for this test run at: /tmp/XenCert-392094cb-be9f-4331-9961-e28e82251814.log
***********************************************************************
Test end time: Fri May  7 15:45:04 2010
Execution time: 47 minutes, 42 seconds.
***********************************************************************
```
- Complete bug-report from the Xenserver installation including logs:
```
[root@xenserver]# xen-bugtool --yestoall
```
**Optional items:**  

- The block and unblock path scripts used by the vendor for multipath failover testing, if different from the samples provided with the kit.  

### Appendix A-Blocking paths for failover testing  

##### iSCSI storage type  
For iSCSI storage type, the paths can be failed over by using the nftables:  

    nft add table inet filter
    nft add chain inet filter input { type filter hook input priority 0 \; }
    nft add chain inet filter output { type filter hook output priority 0 \; }
    nft add rule inet filter output ip daddr <IP address> drop
    nft add rule inet filter input  ip saddr <IP address> drop  

Subsequently, the paths can be brought online as follows:  

    nft flush ruleset 

##### HBA storage type  

The scripts to block and unblock paths for HBA storage type would be vendor specific. A sample script to bring down a qlogic port has been pasted below: 
```
    #!/bin/bash
    ( echo open <qlogic switch name>
     sleep 5
     echo <switch username>
     sleep 1
     echo <switch password>
     sleep 1
     echo admin start
     sleep 1
     echo set port <Post number> state offline
     sleep 1
     echo admin stop
     sleep 1
     echo quit
    ) | telnet

    Similarly to bring a path back up use:

    #!/bin/bash
    ( echo open <qlogic switch name>
      sleep 5
      echo <switch username>
      sleep 1
      echo <switch password>
      sleep 1
      echo admin start
      sleep 1
      echo set port <Post number> state online
      sleep 1
      echo admin stop
      sleep 1
      echo quit
     ) | telnet  
```

### Appendix B-Notes on storage discovery  

To assist the XenServer administrator in determining storage parameters, there are a number of storage information discovery capabilities. These take the form of sr-probe commands executed **on the test host, not on the control host.**  

For example, if the administrator knows the IP address of the NFS server, but does not know the export root, an sr-probe will return the available information in an XML string (Note that the “Error” code is expected).  
```
# xe sr-probe type=nfs device-config:server=172.24.0.90
Error code: SR_BACKEND_FAILURE_101
Error parameters: , The request is missing the serverpath parameter, <?xml version="1.0" ?>
<nfs-exports>
        <Export>
                <Target>
                        172.24.0.90
                </Target>
                <Path>
                        /vhd1
                </Path>
                <Accesslist>
                        *
                </Accesslist>
        </Export>
        <Export>
                <Target>
                        172.24.0.90
                </Target>
                <Path>
                        /XenVMs
                </Path>
                <Accesslist>
                        *
                </Accesslist>
        </Export>
</nfs-exports>
```
The available serverpath parameters are “/vhd1” and “/XenVMs”.

The same is true for using LVM over iSCSI. An sr-probe command will return information useful for establishing the Storage Repository.

Using the same steps, we need to at least know the iSCSI target’s name or IP address.

    # xe sr-probe type=lvmoiscsi device-config:target=172.24.0.90
```
Error code: SR_BACKEND_FAILURE_96
Error parameters: , The request is missing or has an incorrect target IQN parameter, <?xml version="1.0" ?>
<iscsi-target-iqns>
        <TGT>
                <Index>
                        0
                </Index>
                <IPAddress>
                        172.24.0.90
                </IPAddress>
                <TargetIQN>
                        iqn.1997-10.com.snapserver:snaprdmtest1:iscsi0
                </TargetIQN>
        </TGT>
        <TGT>
                <Index>
                        1
                </Index>
                <IPAddress>
                        172.24.0.90
                </IPAddress>
                <TargetIQN>
                        iqn.1997-10.com.snapserver:snaprdmtest1:iscsi1
                </TargetIQN>
        </TGT>
</iscsi-target-iqns>
```
For LVM over HBA SR type, the sr-probe is also similar. However, should a LUN not be zoned in to the host properly, the following discovery shows the adapters but no block devices are listed:  

```
# xe sr-probe type=lvmohba
Error code: SR_BACKEND_FAILURE_90
Error parameters: , The request is missing the device parameter, <?xml version="1.0" ?>
<Devlist>
        <Adapter>
                <host>
                        host4
                </host>
                <name>
                        qla2xxx
                </name>
                <manufacturer>
                        QLogic HBA Driver
                </manufacturer>
                <id>
                        4
                </id>
        </Adapter>
        <Adapter>
                <host>
                        host3
                </host>
                <name>
                        qla2xxx
                </name>
                <manufacturer>
                        QLogic HBA Driver
                </manufacturer>
                <id>
                        3
                </id>
        </Adapter>
        <Adapter>
                <host>
                        host0
                </host>
                <name>
                        mptsas
                </name>
                <manufacturer>
                        LSI Logic Fusion MPT SAS Adapter Driver
                </manufacturer>
                <id>
                        0
                </id>
        </Adapter>
</Devlist>
```
Should the LUN be properly zoned in, the sr-probe will list the block device with its component information. The adapter names listed in the XML returned from the probe, can be used with the –a option to perform a test against only those adapters.  
```
# xe sr-probe type=lvmohba
Error code: SR_BACKEND_FAILURE_90
Error parameters: , The request is missing the device parameter, <?xml version="1.0" ?>
<Devlist>
        <BlockDevice>
                <path>
                        /dev/disk/by-id/scsi-1HITACHI_730157980003
                </path>
                <vendor>
                        HITACHI
                </vendor>
                <serial>
                        730157980003
                </serial>
                <size>
                        22548578304
                </size>
                <adapter>
                        3
                </adapter>
                <channel>
                        0
                </channel>
                <id>
                        1
                </id>
                <lun>
                        0
                </lun>
                <hba>
                        qla2xxx
                </hba>
        </BlockDevice>
        <Adapter>
                <host>
                        host4
                </host>
                <name>
                        qla2xxx
                </name>
                <manufacturer>
                        QLogic HBA Driver
                </manufacturer>
                <id>
                        4
                </id>
        </Adapter>
        <Adapter>
                <host>
                        host3
                </host>
                <name>
                        qla2xxx
                </name>
                <manufacturer>
                        QLogic HBA Driver
                </manufacturer>
                <id>
                        3
                </id>
        </Adapter>
        <Adapter>
                <host>
                        host0
                </host>
                <name>
                        mptsas
                </name>
                <manufacturer>
                        LSI Logic Fusion MPT SAS Adapter Driver
                </manufacturer>
                <id>
                        0
                </id>
        </Adapter>
</Devlist>
```
### Appendix C-Sample scripts provided with shared storage certification kit  

##### blockunblockpaths
This is a script for blocking or unblocking paths manually, which sets the following value in xenstore:  

    /xencert/block-unblock-over = ‘0’

The script then waits for the value above to be set to ‘1’. This provides a hook for users who want to manually fail a set of paths during multipath failover testing. When using this mode, the users are recommended to use the ‘-g’ option to limit the number of failover test iterations to a suitable number.  

You can run the following command on the server to notify the script that the path blocking or unblocking operation is done:  

    xenstore-write /xencert/block-unblock-over ‘1’  

If you do not intend to manually block or unblock the path, use the following alternative scripts blockunblockiscsipaths and blockunblockhbapaths.  

##### blockunblockiscsipaths  

This is a sample script provided with the kit for blocking iSCSI paths during multipath failover testing. The usage of the script is:  

    blockunblockiscsipaths <block/unblock> <noOfPaths> <IP1>,<IP2>,...
The pseudo code for this script can be summarized as:  

If it is a block operation:  
1. Choose noOfPaths paths randomly from the passed in list of IP addresses.  
2. Block the chosen paths using nft command:    
```
nft add table inet filter
nft add chain inet filter input { type filter hook input priority 0 \; }
nft add chain inet filter output { type filter hook output priority 0 \; }
nft add rule inet filter output ip daddr <IP address> drop
nft add rule inet filter input  ip saddr <IP address> drop 
```
3. Write a comma-separated list of blocked IPs to stdout  
If it is an unblock operation, then just unblock the passed in list of IP addresses using nft command:  
```
nft flush ruleset
```
In both the cases, set the ‘/xencert/block-unblock-over’ entry in XenStore to ‘1’ and exit.  

##### blockunblockhbapaths  

This is a sample script provided to bring ports offline and online during multipath failover testing. Sample scripts have been provided for QLogic and Brocade switches. Please rename the relevant script to “blockunblockhbapaths” before testing. 

The script has the following usage:  

    blockunblockhbapaths <block/unblock> <noOfPaths>  
    switch-ip:username:password:port1,port2...

The pseudo code for this script can be summarized as:  

1.Define a value for the number of paths expected to go down, per port blocked by the script (NO_OF_PATHS_PER_PORT). This is important for the kit to know how many paths would go down if a certain number of ports are blocked.  

2.Extract the switch IP, username, password and list of ports from the command line.  

3.If it is a block operation:  
&emsp;&emsp;&emsp;(1). Generate a random number n between 1 and the number of ports passed in less 1.  
&emsp;&emsp;&emsp;(2). Choose n paths randomly from the passed in list of ports.  
&emsp;&emsp;&emsp;(3). Bring the chosen ports down by executing blockunblockHBAport.sh with the following arguments (for Brocade):  

```
blockunblockHBAport.sh ip username password port portdisable
```  

&emsp;&emsp;&emsp;(4). Generate information about paths blocked in the following format and write it to stdout:  

```
ip:username:password:port1,port2….::<number of paths expected to go down>  
```

4.If it’s a unblock operation:  
&emsp;&emsp;&emsp;a.Bring the blocked ports up by executing blockunblockHBAport.sh with the following arguments (for Brocade):  

```
blockunblockHBAport.sh ip username password port portenable  
```  

5.Set the ‘/xencert/block-unblock-over’ entry in XenStore to ‘1’ and exit.   

##### blockunblockHBAport.sh.brocade
This sample script telnets to a brocade switch and brings ports up or down. The script is packaged with the kit, and looks like:
```
#!/bin/bash
( echo open ${1}
  sleep 5
  echo ${2}
  sleep 1
  echo ${3}
  sleep 1
  echo ${5} ${4}
  sleep 1
  sleep 1
  echo exit 
 ) | telnet
```
##### blockunblockHBAport.sh.qlogic
This sample script telnets to a QLogic SANbox switch and brings ports up or down. The script is packaged with the kit, and looks like:

        #!/bin/bash
        ( echo open ${1}
          sleep 5
          echo ${2}
          sleep 1
          echo ${3}
          sleep 1
          echo admin start
          sleep 1
          echo set port ${4} state ${5}
          sleep 1
          echo admin stop
          sleep 1
          echo quit 
         ) | telnet


##### blockunblockHBAport.sh.cisco  

This sample script telnets to a cisco switch and brings ports up or down. The script is packaged with the kit, and looks like:  

         #!/bin/bash
         ( echo open ${1}
           sleep 5
           echo ${2}
           sleep 1
           echo ${3}
           sleep 1
           echo config t
           sleep 1
           echo int fc1/${4}
           sleep 1
           echo ${5}
           sleep 1
           cho exit
          sleep 1
           echo quit 
          ) | telnet  

 

### Appendix D-Sample commands for testing multipathing with shared storage certification kit

The following illustrations assume that shared storage certification kit has been installed at /root/XenCert. If this is not true, then please replace “/root/XenCert” in all the examples with the respective path.  

##### iSCSI
For 100 iterations of multipathing failover test:  

    ./XenCert –b lvmoiscsi –t 10.20.345.67 –q xx.xx.xx.xx.xx –m –u /root/XenCert/blockunblockiscsipaths 

For less than 100 iterations of multipathing failover test, for instance for 10 iterations:  

    ./XenCert –b lvmoiscsi –m –u /root/XenCert/blockunblockiscsipaths –g 10

##### HBA

This section further assumes that the scripts blockunblockhbapaths-brocade, blockunblockhbapaths-qlogic, blockunblockHBAPort-brocade.sh and blockunblockHBAPort-qlogic.sh, have been tested to working for the respective storage type, and performing as expected. The port numbers as mentioned in the example below are as they appear on the switch.  

###### QLogic 
For 100 iterations of multipathing failover test:  

    ./XenCert –b lvmohba –m –u <full path>/blockunblockhbapaths-qlogic –i fc-switch-ip:username:password:<port1>,<port2> -S <SCSIID1, SCSIID2,…>  

For less than 100 iterations of multipathing failover test, for instance for 10 iterations:
    ./XenCert –b lvmohba –m –u <full path>/XenCert/blockunblockhbapaths-qlogic –i fc-switch-ip:username:password:<port1>,<port2> -S <SCSIID1, SCSIID2,…> -g 10  

###### Brocade 
For 100 iterations of multipathing failover test:  

    ./XenCert –b lvmohba –m –u <full path>/XenCert/blockunblockhbapaths-brocade –i fc-switch-ip:username:password:<port1>,<port2> -S <SCSIID1, SCSIID2,…>  

For less than 100 iterations of multipathing failover test, for instance for 10 iterations:  

    ./XenCert –b lvmohba –m –u <full path>/XenCert/blockunblockhbapaths-brocade –i fc-switch-ip:username:password:<port1>,<port2> -S <SCSIID1, SCSIID2,…> -g 10  

###### Cisco 
For 100 iterations of multipathing failover test:  

    ./XenCert –b lvmohba –m –u <full path>/XenCert/blockunblockhbapaths-cisco –i fc-switch-ip:username:password:<port1>,<port2> -S <SCSIID1, SCSIID2,…>  

For less than 100 iterations of multipathing failover test, for instance for 10 iterations:  

    ./XenCert –b lvmohba –m –u <full path>/XenCert/blockunblockhbapaths-cisco –i fc-switch-ip:username:password:<port1>,<port2> -S <SCSIID1, SCSIID2,…>  -g 10
 

#### Notice and Disclaimer <!-- omit in toc -->
<font size="2">The contents of this kit are subject to change without notice.

Copyright © 2026 Cloud Software Group, Inc. This kit allows you to test your products for compatibility with XenServer products.  Actual compatibility results may vary.  The kit is not designed to test for all compatibility scenarios.  Should you use the kit, you must not misrepresent the nature of the results to third parties. TO THE EXTENT PERMITTED BY APPLICABLE LAW, XENSERVER MAKES AND YOU RECEIVE NO WARRANTIES OR CONDITIONS, EXPRESS, IMPLIED, STATUTORY OR OTHERWISE, AND XENSERVER SPECIFICALLY DISCLAIMS WITH RESPECT TO THE KIT ANY CONDITIONS OF QUALITY, AVAILABILITY, RELIABILITY, BUGS OR ERRORS, AND ANY IMPLIED WARRANTIES, INCLUDING, WITHOUT LIMITATION, ANY WARRANTY OF MERCHANTABILITY OR FITNESS FOR A PARTICULAR PURPOSE. YOU ASSUME THE RESPONSIBILITY FOR ANY INVESTMENTS MADE OR COSTS INCURRED TO ACHIEVE YOUR INTENDED RESULTS. TO THE EXTENT PERMITTED BY APPLICABLE LAW, XENSERVER SHALL NOT BE LIABLE FOR ANY DIRECT, INDIRECT, SPECIAL, CONSEQUENTIAL, INCIDENTAL, PUNITIVE OR OTHER DAMAGES (INCLUDING, WITHOUT LIMITATION, DAMAGES FOR LOSS OF INCOME, LOSS OF OPPORTUNITY, LOST PROFITS OR ANY OTHER DAMAGES), HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, AND WHETHER OR NOT FOR NEGLIGENCE OR OTHERWISE, AND WHETHER OR NOT XENSERVER HAS BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.</font>
