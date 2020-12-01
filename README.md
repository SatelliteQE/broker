# Broker ![update_broker_image](https://github.com/SatelliteQE/broker/workflows/update_broker_image/badge.svg)
The infrastrucure middleman

# Description
Broker is a tool designed to provide a common interface between one or many services that provision virtual machines. It is an abstraction layer that allows you to ignore (most) of the implementation details and just get what you need.

# Installation
```
dnf install cmake
cd <broker root directory>
pip install .   or   pip install broker
cp broker_settings.yaml.example broker_settings.yaml
```
Then edit the broker_settings.yaml file

Broker can also be ran outside of its base directory. In order to do so, specify the directory broker's files are in with the
`BROKER_DIRECTORY` envronment variable.
```BROKER_DIRECTORY=/home/jake/Programming/broker/ broker inventory```

# Configuration
The broker_settings.yaml file is used, through DynaConf, to set configuration values for broker's interaction with its 'providers'.

DynaConf integration provides support for setting environment variables to override any settings from the yaml file.

An environment variable override would take the form of: `DYNACONF_AnsibleTower__base_url="https://my.ansibletower.instance.com"`. Note the use of double underscores to model nested maps in yaml.

For the AnsibleTower provider, authentication can be achieved either through setting a username and password, or through a token (Personal Access Token in Tower).

A username can still be provided when using a token to authenticate. This user will be used for inventory sync (examples below). This may be helpful for AnsibleTower administrators who would like to use their own token to authenticate, but want to set a different user in configuration for checking inventory.

# Usage
**Checking out a VM**
```
broker checkout --workflow "workflow-name" --workflow-arg1 something --workflow-arg2 else
```
You can pass in any arbitrary arguments you want. Broker can also checkout multiple VMs at once by specifying a count.
```
broker checkout --nick rhel7 --count 3
```

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
broker duplicate 0 --count 2
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
To sync inventory for a specific user, use the following syntax with `--sync`.
```
broker inventory --sync AnsibleTower:<username>
```

**Extending your VM lease time**

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

**Gaining information about Broker's providers**

Broker's `providers` command allows you to gather information about what providers are avaiable as well as each providers actions. Additionally, you can find out information about different arguments for a provider's action with this command.
```
broker providers --help
broker providers AnsibleTower --help
broker providers AnsibleTower --workflows
broker providers AnsibleTower --workflow remove-vm
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

**Filter hosts for Broker actions**

Actions that Broker can take against hosts (checkin, duplicate, extend) can take in a filter argument. This filter will decide which hosts the actions are applied to. A filter by itself will not select hosts for these actions, you will still need to specify which hosts to act against, or use `--all` when available. From there, the filter decides which of those hosts make it through to be acted upon.

Broker's filters are based on what is stored in its local inventory file. Therefore, only properties in that file are filter-able. Nested properties are annotated with a `.` notation. For example, a top-level property `hostname` can be accessed by itself. However, a nested property of `_broker_args` called `version` would be accessed by `_broker_args.version`.

Filters take the form `"(property)(condition)(value)"`. Filters have several possible conditions:
 - `<` means "in" or that the filter value exists within the actual value
 - `=` means "equals"
 - `{` means "starts with"
 - `}` means "ends with"

Furthermore, putting a `!` before the condition inverts the filter. So `!=` means "not equals" and `!<` means "not in".

**Example filters:**

`--filter 'hostname<test'` The string test should exist somewhere in the hostname value
`--filter '_broker_args.template{deploy-sat'` The template should start with the string "deploy-sat"

You can also chain multiple filters together by separating them with a comma. These are additive AND filters where each filter condition must match.

`--filter 'name<test,_broker_args.provider!=RHEV'` The host's name should have test in it and the provider should not equal RHEV.

**Note:** Due to shell expansion, it is recommended to wrap a filter in single quotes.