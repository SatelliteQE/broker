# Broker
The infrastrucure middleman

# Description
Broker is a tool designed to provide a common interface between one or many services that provision virtual machines. It is an abstraction layer that allows you to ignore (most) of the implementation details and just get what you need.

# Installation
```
cd <broker root directory>
pip install .
mv setting.yaml.example settings.yaml
```
Then edit the settings.yaml file

# Usage
**Checking out a VM**
```
broker checkout --workflow "workflow-name" --workflow-arg1 something --workflow-arg2 else
```
You can pass in any arbitrary arguments you want

**Nicks**
Broker allows you to define configurable nicknames for checking out vms. Just add yours to setting.yaml and call with the ```--nick``` option
```
broker checkout --nick rhel7
```
**Listing your VMs**
Broker maintains a local inventory of the VMs you've checked out. You can see these with the ```inventory``` command.
```
broker inventory
```

**Checking in VMs**
You can also return a VM to its provider with the ```checkin``` command.
You may use either the local id (```broker inventory```), the hostname, or "all" to checkin everything.
```
broker checkin my.host.fqdn.com
broker checkin 0
broker checkin 1 3 my.host.fqdn.com
broker checkin all
```
