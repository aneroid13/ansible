#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2019, Anton Bayandin (@aneroid13)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

ANSIBLE_METADATA = {
    'metadata_version': '0.1',
    'status': ['preview'],
    'supported_by': 'community'
}

DOCUMENTATION = r'''
---
module: vmware_guest_rdm
short_description: Connect or disconnect virtual machine raw data mapped (RDM) disks.
description:
    - This module can be used to create add or remove RDM disks belonging to given virtual machine.
    - All parameters and VMware object names are case sensitive.
    - RDM device must be attached to VM's ESXi host before addition ! 
    - Best practice is attach RDM device to every cluster host. (for easy VM migration)
    - You can manage which controllers will be used for RDM disks connection by 'rdm_controller_bus' option.
    - Non existing controllers will be create, if they presented in 'rdm_controller_bus'.
    - Maximum available configuration for 'rdm_controller_bus' is [0, 1, 2, 3]
    - RDM controlles type must be defined by 'rdm_controller_type', paravirtual is default type. 
    - All RDM controllers (defined by 'rdm_controller_type') must be the same type.
    - Maximum slots in paravirtual controller = (15 for VM version < 14) or (64 for VM version >= 14) according to https://configmax.vmware.com
    - RDM compatibility mode - physical.
    - Operating user must has ability to create files on VM's datastore (for RDM link creation).
    - RDM disk will be remove by wwid, in spite of rdm_controller_bus parameter.
    - Be careful while removing disk specified as this may lead to data loss.
version_added: '2.9'
author:
    - Anton Bayandin (@aneroid13)
notes:
    - Tested on vSphere 6.0 and 6.7.
requirements:
    - "python >= 2.7"
    - PyVmomi
options:
   name:
     description:
     - Name of the virtual machine.
     - This is required parameter, if parameter C(uuid) is not supplied.
     type: str
   uuid:
     description:
     - UUID of the instance to gather information if known, this is VMware's unique identifier.
     - This is required parameter, if parameter C(name) is not supplied.
     type: str
   folder:
     description:
     - Destination folder, absolute or relative path to find an existing guest.
     - This is required parameter, only if multiple VMs are found with same name.
     - The folder should include the datacenter. ESX's datacenter is ha-datacenter
     type: str
   datacenter:
     description:
     - The datacenter name to which virtual machine belongs to.
     required: False
     type: str
    state:
      description:
      - Describe action to do with disk. Present means attach RDM disk. Absent - detach RDM disk.
      type: str
      required: True
      choices: [absent, present]
    rdm_controller_type: 
      description:
      - Disk controller type.
      - All controllers used for RDM disk must be same type.
      - Already existing controllers defined in 'rdm_controller_bus' must be 'rdm_controller_type' type.
      type: str
      default: paravirtual
      choices: [paravirtual, lsilogic, buslogic, lsilogicsas]
    rdm_controller_bus:
      description:
      - List of controller buses, where RDM disk can be attached.
      - Controller must be same type as 'rdm_controller_type' described.
      - Absent controller, mentioned in list, will be created.
      - RDM disk detach by wwid, from any controller, in spite of bus number.
      type: list
      required: True
    rdm_disk:
      description:
      - List of RDM to operate with.
      - Each RDM disk must contains property 'wwid'
      type: list
      required: True
extends_documentation_fragment: vmware.documentation

'''

EXAMPLES = r'''
    - name: Connect vm RDM disks
      vmware_guest_rdm:
        hostname: "vc-test.xyz.local"
        username: "my-user"
        password: "my-password"
        port: 443
        validate_certs: False
        name: "test_server"
        datacenter: "DC-1"
        state: present
        rdm_controller_type: paravirtual
        rdm_controller_bus: [1, 2]
        rdm_disk: 
           - wwid: "60002AC0000000000000006A000123FD"
           - wwid: "60002AC0000000000000006B000123FD"
           - wwid: "60002AC0000000000000006C000123FD"
           - wwid: "60002AC0000000000000006D000123FD"
      delegate_to: localhost
      
      
      
  - name: Remove vm RDM disks
    vmware_guest_rdm:
      hostname: '{{ vCenter }}'
      username: '{{ VMWlogin }}'
      password: '{{ VMWpass }}'
      port: 443
      validate_certs: False
      name: '{{ inventory_hostname }}'
      datacenter: DC-1
      state: absent
      rdm_controller_type: paravirtual
      rdm_controller_bus: [1]
      rdm_disk: 
        - wwid: "60002AC0000000000000006A000123FD"
        - wwid: "60002AC0000000000000006B000123FD"
        - wwid: "60002AC0000000000000006C000123FD"
        - wwid: "60002AC0000000000000006D000123FD"
    delegate_to: localhost
'''

RETURN = r'''
'''

try:
    from pyVmomi import vim, vmodl
except ImportError: pass
import re, time
# from ansible.module_utils import basic
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.vmware import PyVmomi, vmware_argument_spec, gather_vm_facts



class PyVmomiDeviceHelper(object):
    """ This class is a helper to create easily VMWare Objects for PyVmomiHelper """

    def __init__(self, module):
        self.module = module

    @staticmethod
    def create_scsi_controller_bus(scsi_type, bus_n):
        scsi_ctl = vim.vm.device.VirtualDeviceSpec()
        scsi_ctl.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        if scsi_type == 'lsilogic':
            scsi_ctl.device = vim.vm.device.VirtualLsiLogicController()
        elif scsi_type == 'paravirtual':
            scsi_ctl.device = vim.vm.device.ParaVirtualSCSIController()
        elif scsi_type == 'buslogic':
            scsi_ctl.device = vim.vm.device.VirtualBusLogicController()
        elif scsi_type == 'lsilogicsas':
            scsi_ctl.device = vim.vm.device.VirtualLsiLogicSASController()

        scsi_ctl.device.deviceInfo = vim.Description()
        scsi_ctl.device.slotInfo = vim.vm.device.VirtualDevice.PciBusSlotInfo()
        scsi_ctl.device.slotInfo.pciSlotNumber = 16
        scsi_ctl.device.controllerKey = 100
        scsi_ctl.device.unitNumber = 3
        scsi_ctl.device.busNumber = bus_n
        scsi_ctl.device.hotAddRemove = True
        scsi_ctl.device.sharedBus = 'noSharing'
        scsi_ctl.device.scsiCtlrUnitNumber = 7

        return scsi_ctl

    @staticmethod
    def is_scsi_controller(device):
        return isinstance(device, vim.vm.device.VirtualLsiLogicController) or \
            isinstance(device, vim.vm.device.ParaVirtualSCSIController) or \
            isinstance(device, vim.vm.device.VirtualBusLogicController) or \
            isinstance(device, vim.vm.device.VirtualLsiLogicSASController)

    def create_rdm_disk(self, scsi_key, lun, ds, fname, disk_index=None, compat='physicalMode', diskmode='independent_persistent'):
        RDMDiskSpec = vim.vm.device.VirtualDeviceSpec()
        RDMDiskSpec.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        RDMDiskSpec.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.create
        RDMDiskSpec.device = vim.vm.device.VirtualDisk()
        RDMDiskSpec.device.key = -100 - disk_index
        RDMDiskSpec.device.controllerKey = scsi_key
        RDMDiskSpec.device.unitNumber = disk_index
        RDMDiskSpec.device.backing = vim.vm.device.VirtualDisk.RawDiskMappingVer1BackingInfo()
        RDMDiskSpec.device.backing.lunUuid = lun.uuid
        RDMDiskSpec.device.backing.deviceName = lun.deviceName
        RDMDiskSpec.device.backing.compatibilityMode = compat
        RDMDiskSpec.device.backing.diskMode = diskmode
        RDMDiskSpec.device.backing.datastore = ds
        RDMDiskSpec.device.backing.fileName = fname
        RDMDiskSpec.device.backing.sharing = 'sharingNone'
        return RDMDiskSpec

    def remove_rdm_disk(self, rdm):
        RDMDiskSpecRem = vim.vm.device.VirtualDeviceSpec()
        RDMDiskSpecRem.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
        RDMDiskSpecRem.fileOperation = vim.vm.device.VirtualDeviceSpec.FileOperation.destroy
        RDMDiskSpecRem.device = vim.vm.device.VirtualDisk()
        RDMDiskSpecRem.device.key = rdm.key
        RDMDiskSpecRem.device.controllerKey = rdm.controllerKey
        RDMDiskSpecRem.device.unitNumber = rdm.unitNumber
        return RDMDiskSpecRem

class PyVmomiHelper(PyVmomi):
    def __init__(self, module):
        super(PyVmomiHelper, self).__init__(module)
        self.device_helper = PyVmomiDeviceHelper(self.module)
        self.configspec = None
        self.change_detected = False
        self.customspec = None
        self.facts = None
        self.rdm_state = self.params['state']
        self.rdm_disks = []
        self.ctl_list = self.params['rdm_controller_bus']
        self.ctl_type = self.params['rdm_controller_type']
        self.ctl_max = 15
        self.ctl_slot_key = {}
        self.ctl_key_slot = {}
        self.ctl_units = {}
        self.disk_units = {}

    def gather_facts(self, vm):
        return gather_vm_facts(self.content, vm)

    def check_maximums(self):
        vm_version = int(str(self.facts['hw_version']).replace("vmx-",""))
        if vm_version >= 14 and self.ctl_type == 'paravirtual':
            self.ctl_max = 64

    def get_host_by_vm(self, vm_obj):
        container = self.content.viewManager.CreateListView([vm_obj.runtime.host])
        obj_list = container.view
        container.Destroy()
        if obj_list is not None:
            return obj_list[0]
        else:
            self.module.fail_json(msg="Your don't have read permissions on VM host object.")

    def get_datastore_by_vm(self, vm_obj):
        if len(vm_obj.datastore) is not 0:
            return vm_obj.datastore[0]
        else:
            self.module.fail_json(msg="You don't have available permission on VM datastore object.")

    def vm_get_ctls(self, vm_obj):
        # If vm_obj doesn't exist there is no SCSI controller to find
        if vm_obj is None:
            return None

        for device in vm_obj.config.hardware.device:
            if self.device_helper.is_scsi_controller(device):
                self.ctl_slot_key[device.busNumber] = device.key
                self.ctl_key_slot[device.key] = device.busNumber
                self.ctl_units[device.busNumber] = str(type(device).__name__).lower().replace("vim.vm.device.", "")

    def ctl_check_types(self, search):
        for bus, bus_type in self.ctl_units.items():
            if bus in self.ctl_list:
                if str.find(bus_type, search) == -1:
                    self.module.fail_json(msg="VM has controller of wrong type already. "
                                              "You must remove it manually, change type or select another before proceed.")

    def ctl_create_new(self, ctl_type):
        for ctl in self.ctl_list:
            if ctl not in self.ctl_units.keys():
                if self.get_vm_ctl_n(self.current_vm_obj, ctl) is None:
                    scsi_ctl_bus_n = self.device_helper.create_scsi_controller_bus(ctl_type, ctl)
                    self.change_detected = True
                    self.configspec.deviceChange.append(scsi_ctl_bus_n)

    def get_vm_ctl_n(self, vm_obj, bus_n):
        # If vm_obj doesn't exist there is no SCSI controller to find
        if vm_obj is None:
            return None

        for device in vm_obj.config.hardware.device:
            if self.device_helper.is_scsi_controller(device):
                if device.busNumber == bus_n:
                    scsi_ctl = vim.vm.device.VirtualDeviceSpec()
                    scsi_ctl.device = device
                    return scsi_ctl

        return None

    def get_rdm_info(self, rdms):
        wwid_presented = 0
        used_disks = len(rdms)
        rdms_requested = self.params.get('rdm_disk')

        for c in self.ctl_units.keys():
            self.disk_units[c] = {7}  # 7 slot used by controller themselves, reserve it

        for disk in rdms:
            ctl_n = self.ctl_key_slot[disk.controllerKey]
            self.disk_units[ctl_n].add(disk.unitNumber)

            for rdm_check in rdms_requested:
                if str.find(str(disk.backing.lunUuid).lower(), str(rdm_check['wwid']).lower()) >= 0:
                    wwid_presented += 1
                    break

        new_disks = len(self.params.get('rdm_disk')) - wwid_presented
        all_disk = used_disks + new_disks
        free_bays = self.ctl_max * len(self.ctl_list)

        if all_disk > free_bays and self.rdm_state == 'present':
            self.module.fail_json(msg="Disk number ({all}) bigger than number of free bays ({bays})."
                                      "Add another contoller or reduce disk quantity.".format(all=all_disk, bays=free_bays))

    def create_rdm(self, lun, vm_ds, rdm_file):
        class FreeBusFound(Exception): pass
        try:
            for ctl in self.ctl_list:
                for bus_n in range(self.ctl_max + 1):            # cycle all slot to find empty
                    if bus_n not in self.disk_units[ctl]:       # find empty slot
                        raise FreeBusFound
        except FreeBusFound:
            self.disk_units[ctl].add(bus_n)                     # bus reserved
            diskspec = self.device_helper.create_rdm_disk(self.ctl_slot_key[ctl], lun, vm_ds, rdm_file, bus_n)
            self.configspec.deviceChange.append(diskspec)
            self.change_detected = True

    def configure_rdm_disks(self, vm_obj):
        # Get vm referenced objects
        vm_host = self.get_host_by_vm(vm_obj)
        vm_host_luns = vm_host.configManager.storageSystem.storageDeviceInfo.scsiLun
        vm_ds = self.get_datastore_by_vm(vm_obj)
        vm_vmx_path = vm_obj.config.files.vmPathName

        # Disk attach operations
        if self.rdm_state == 'present':
            class DiskPresented(Exception): pass

            for expected_disk_spec in self.params.get('rdm_disk'):
                wwid = expected_disk_spec['wwid']
                #TODO: make virtualMode compatibilityMode

                # Find out LUN UUID
                try:
                    lun = [l for l in vm_host_luns if l.canonicalName == "naa." + str(wwid).lower()][0]
                except IndexError:
                    self.module.fail_json(msg="LUN %s not presented to host" % wwid)

                # Check if RDM already presented
                try:
                    if vm_obj is not None and self.rdm_disks is not None:
                        for rdm in self.rdm_disks:
                            if lun.uuid == rdm.backing.lunUuid:
                                raise DiskPresented
                except DiskPresented:         # Skip lun cycle, disk already presented
                    continue

                wwn_id_patt = re.compile('^naa\.[0-9a-z]+0000+')
                rdmid = re.split(wwn_id_patt, lun.canonicalName)[1]
                rdm_file = vm_vmx_path.replace(".vmx", "_RDM" + str(rdmid) + ".vmdk")
                self.create_rdm(lun, vm_ds, rdm_file)

        # Disk detach operations
        if self.rdm_state == 'absent':
            for expected_disk_spec in self.params.get('rdm_disk'):
                wwid = expected_disk_spec['wwid']

                try:
                    lun = [l for l in vm_host_luns if l.canonicalName == "naa." + str(wwid).lower()][0]
                except IndexError:
                    self.module.fail_json(msg="LUN %s not presented to host" % wwid)

                for rdm in self.rdm_disks:
                    if rdm.backing.lunUuid == lun.uuid:
                        diskspec = self.device_helper.remove_rdm_disk(rdm)
                        self.configspec.deviceChange.append(diskspec)
                        self.change_detected = True

    @staticmethod
    def wait_for_task(task):
        while task.info.state not in ['error', 'success']:
            time.sleep(1)

    def apply_changes(self):
        task = None
        try:
            task = self.current_vm_obj.ReconfigVM_Task(spec=self.configspec)
        except vim.fault.RestrictedVersion as e:
            self.module.fail_json(msg="Failed to reconfigure virtual machine due to"
                                      " product versioning restrictions: %s" % str(e.msg))
        self.wait_for_task(task)

        if task.info.state == 'error':
            # https://kb.vmware.com/selfservice/microsites/search.do?language=en_US&cmd=displayKC&externalId=2021361
            # https://kb.vmware.com/selfservice/microsites/search.do?language=en_US&cmd=displayKC&externalId=2173
            return task.info.error.msg

    def reconfigure_vm(self):
        change_applied = False
        self.facts = self.gather_facts(self.current_vm_obj)
        self.check_maximums()

        # Good controller need for disk presentation.
        self.vm_get_ctls(self.current_vm_obj)
        if self.rdm_state == 'present':
            self.configspec = vim.vm.ConfigSpec()
            self.configspec.deviceChange = []
            # Check and create controller if it's not exist.
            self.ctl_check_types(self.ctl_type)
            self.ctl_create_new(self.ctl_type)

            # Apply new controller configuration immediately
            if self.change_detected:
                res = self.apply_changes()
                if res:
                    return {'changed': True, 'failed': True, 'msg': res }
                else:
                    change_applied = True
                    self.vm_get_ctls(self.current_vm_obj)

        # Add disks to controller
        self.configspec = vim.vm.ConfigSpec()
        self.configspec.deviceChange = []
        self.rdm_disks = [dev for dev in self.current_vm_obj.config.hardware.device if
          type(dev.backing) == vim.vm.device.VirtualDisk.RawDiskMappingVer1BackingInfo]
        self.get_rdm_info(self.rdm_disks)
        self.configure_rdm_disks(vm_obj=self.current_vm_obj)

        # Apply configuration changes
        if self.change_detected:
            res = self.apply_changes()
            if res:
                return {'changed': True, 'failed': True, 'msg': res}
            else:
                change_applied = True

        self.facts = self.gather_facts(self.current_vm_obj)
        return {'changed': change_applied, 'failed': False, 'instance': self.facts}


def main():
    argument_spec = vmware_argument_spec()
    argument_spec.update(
        name=dict(type='str'),
        uuid=dict(type='str'),
        folder=dict(type='str'),
        datacenter=dict(type='str', required=False),
        state=dict(type='str', required=True,
                   choices=['absent', 'present'] ),
        rdm_controller_type=dict(type='str', default='paravirtual',
                                 choices=['paravirtual', 'lsilogic', 'buslogic', 'lsilogicsas'] ),
        rdm_controller_bus=dict(type='list', required=True),
        rdm_disk=dict(type='list', required=True)
    )
    module = AnsibleModule(argument_spec=argument_spec,
                           required_one_of=[['name', 'uuid']])

    if module.params['folder']:
        # FindByInventoryPath() does not require an absolute path
        # so we should leave the input folder path unmodified
        module.params['folder'] = module.params['folder'].rstrip('/')

    pyv = PyVmomiHelper(module)
    # Check if the VM exists before continuing
    vm = pyv.get_vm()

    if vm:
        # VM exists
        try:
            result = pyv.reconfigure_vm()
            module.exit_json(**result)
        except Exception as exc:
            module.fail_json(msg="Failed to attach disk with exception : {exeption}".format(exeption=exc))
    else:
        # We unable to find the virtual machine user specified
        module.fail_json(msg="Unable to attach disk for non-existing VM %s" % (
                    module.params.get('uuid') or module.params.get('name')))


if __name__ == '__main__':
    main()
