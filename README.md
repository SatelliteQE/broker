# Broker
The infrastrucure middleman

# Description
Broker is a tool designed to provide a common interface between one or many services that provision virtual machines. It is an abstraction layer that allows you to ignore (most) of the implementation details and just get what you need.

# Installation
```
dnf install cmake
cd <broker root directory>
pip install .
cp broker_settings.yaml.example broker_settings.yaml
```
Then edit the broker_settings.yaml file

Broker can also be ran outside of its base directory. In order to do so, specify the directory broker's files are in with the
`BROKER_DIRECTORY` envronment variable.
```BROKER_DIRECTORY=/home/jake/Programming/broker/ broker inventory```

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

**Duplicating a VM**

Broker offers another shortcut for checking out a VM with the same recipe as one already checked out by Broker. This is via the ```duplicate``` command.
```
broker duplicate my.awesome.vm.com
broker duplicate 0
broker duplicate 1 3
```

**Listing your VMs**

Broker maintains a local inventory of the VMs you've checked out. You can see these with the ```inventory``` command.
```
broker inventory
```
To sync your inventory from a supported provider, use the `--sync` option.
```
broker inventory --sync AnsibleTower
```

**Extending your VM lease time***
Providers supporting extending a VM's lease time make that functionality available through the `extend` subcommand.
```
broker extend 0
broker extend hostname
broker extend vmname
broker extend --all
```

**Checking in VMs**
You can also return a VM to its provider with the ```checkin``` command.
You may use either the local id (```broker inventory```), the hostname, or "all" to checkin everything.
```
broker checkin my.host.fqdn.com
broker checkin 0
broker checkin 1 3 my.host.fqdn.com
broker checkin --all
```

**Creating nicks**
Broker will attempt to help your create your own nicks with the ```nick-help``` command.
If supported by your chosen provider, nick-help will display the additional arguments you can use when defining a new nick.
```
broker nick-help --help
broker nick-help --workflow my-awesome-workflow
```
Additionally, if you're unfamiliar with what actions are supported by your provider, you can get a list by passing the provider's name.
```
broker nick-help --provider AnsibleTower
```

**Run arbitrary actions**
If a provider action doesn't result in a host creation/removal, Broker allows you to execute that action as well. There are a few output options available as well.
```
broker execute --help
broker execute --workflow my-awesome-workflow --additional-arg True
broker execute -o raw --workflow my-awesome-workflow --additional-arg True
broker execute -o raw --workflow my-awesome-workflow --additional-arg True --artifacts last
```

**Run Broker in the background**
Certain Broker actions can be run in the background, these currently are: checkout, checkin, duplicate, and execute. When running a command in this mode, it will spin up a new Broker process and no longer log to stderr. To check progress, you can still follow broker's log file.
Note that background mode will interfere with output options for execute since it won't be able to print to stdout. Those should kept in log mode.
```
broker checkout --background --nick rhel7
broker checkin -b --all
broker duplicate -b 0
broker execute -b --workflow my-awesome-workflow --artifacts
```
