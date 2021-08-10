[![PythonPackage](https://github.com/SatelliteQE/broker/actions/workflows/python-publish.yml/badge.svg)](https://github.com/SatelliteQE/broker/actions/workflows/python-publish.yml)
[![ContainerImage](https://github.com/SatelliteQE/broker/actions/workflows/update_broker_image.yml/badge.svg)](https://github.com/SatelliteQE/broker/actions/workflows/update_broker_image.yml)
[![CodeQL](https://github.com/SatelliteQE/broker/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/SatelliteQE/broker/actions/workflows/codeql-analysis.yml)
# Broker
The infrastrucure middleman


# Description
Broker is a tool designed to provide a common interface between one or many services that provision virtual machines or containers. It is an abstraction layer that allows you to ignore most of the implementation details and just get what you need.

# Installation
```
dnf install cmake
cd <broker root directory>
pip install .   or   pip install broker
cp broker_settings.yaml.example broker_settings.yaml
```
Then edit the broker_settings.yaml file

If you are using the Container provider, then install the extra dependency based on your container runtime of choice.
```
pip install broker[podman]
or
pip install broker[docker]
```
These may not work correctly in non-bash environments.

Broker can also be ran outside of its base directory. In order to do so, specify the directory broker's files are in with the
`BROKER_DIRECTORY` envronment variable.
```BROKER_DIRECTORY=/home/jake/Programming/broker/ broker inventory```

# Configuration
The broker_settings.yaml file is used, through DynaConf, to set configuration values for broker's interaction with its 'providers'.

DynaConf integration provides support for setting environment variables to override any settings from broker's config file.

An environment variable override would take the form of: `BROKER_AnsibleTower__base_url="https://my.ansibletower.instance.com"`. Note the use of double underscores to model nested maps in yaml.

Broker allows for multiple instances of a provider to be in its config file. You can name an instance anything you want, then put instance-specfic settings nested under the instance name. One of your instances must have a setting `default: True`.

For the AnsibleTower provider, authentication can be achieved either through setting a username and password, or through a token (Personal Access Token in Tower).

A username can still be provided when using a token to authenticate. This user will be used for inventory sync (examples below). This might be helpful for AnsibleTower administrators who would like to use their own token to authenticate, but want to set a different user in configuration for checking inventory.

# CLI Usage
**Checking out a VM or container**
```
broker checkout --workflow "workflow-name" --workflow-arg1 something --workflow-arg2 else
```
You can pass in any arbitrary arguments you want. Broker can also checkout multiple VMs at once by specifying a count.
```
broker checkout --nick rhel7 --count 3
```
To specify an instance a checkout should be performed against, pass a flag name matching your provider class and a value matching the instance name.
```
broker checkout --nick rhel7 --AnsibleTower testing
```
If you have more complex data structures you need to pass in, you can do that in two ways.
You can populate a json or yaml file where the top-level keys will become broker arguments and their nested data structures become values.
```
broker checkout --nick rhel7 --args-file tests/data/broker_args.json
```
You can also pass in a file for other arguments, where the contents will become the argument's value
```
broker checkout --nick rhel7 --extra tests/data/args_file.yaml
```
**Note:** Check with the provider to determine specific arguments.

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

**Listing your VMs and containers**

Broker maintains a local inventory of the VMs and containers you've checked out. You can see these with the ```inventory``` command.
```
broker inventory
```
To sync your inventory from a supported provider, use the `--sync` option.
```
broker inventory --sync AnsibleTower
```
To sync an inventory for a specific user, use the following syntax with `--sync`.
```
broker inventory --sync AnsibleTower:<username>
```
To sync an inventory for a specific instance, use the following syntax with --sync.
```
broker inventory --sync Container::<instance name>
```
This can also be combined with the user syntax above.
```
broker inventory --sync Container:<username>::<instance name>
```


**Extending your VM lease time**

Providers supporting extending a VM's lease time make that functionality available through the `extend` subcommand.
```
broker extend 0
broker extend hostname
broker extend vmname
broker extend --all
```

**Checking in VMs and containers**

You can also return a VM to its provider with the ```checkin``` command.
Containers checked in this way will be fully deleted regardless of its status.
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
When executing with the Container provider, a new container will be spun up with your command (if specified), ran, and cleaned up.
```
broker execute --help
broker execute --workflow my-awesome-workflow --additional-arg True
broker execute -o raw --workflow my-awesome-workflow --additional-arg True
broker execute -o raw --workflow my-awesome-workflow --additional-arg True --artifacts last
```

**machine processable output**

If running in a CI or other automated environment, Broker offers the choice to store important output information in an output file. This is json-formatted data. Please be aware that any existing file with the matching path and name will be erased.
```
broker --output-file output.json checkout --nick rhel7
broker --output-file inventory.json inventory
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

# API Usage
**Basics:**

Broker also exposes most of the same functionality the CLI provides through a Broker class.
To use this class, simply import:
```python
from broker import Broker
```
The Broker class largely accepts the same arguments as you would pass via the CLI. One key difference is that you need to use underscores instead of dashes. For example, a checkout at the CLI that looks like this
```
broker checkout --nick rhel7 --args-file tests/data/broker_args.json
```
could look like this in an API usage
```python
rhel7_host = Broker(nick="rhel7", args_file="tests/data/broker_args.json").checkout()
```
Broker will carry out its usual actions and package the resulting host in a Host object. This host object will also include some basic functionality, like the ability to execute ssh commands on the host.
Executed ssh command results are packaged in a Results object containing status (return code), stdout, and stderr.
```python
result = rhel7_host.execute("rpm -qa")
assert result.status == 0
assert "my-package" in result.stdout
```


**Recommended**

The Broker class has a built-in context manager that automatically performs a checkout upon enter and checkin upon exit. It is the recommended way of interacting with Broker for host management.
In the below two lines of code, a container host is created (pulled if needed or applicable), a broker Host object is constructed, the host object runs a command on the container, output is checked, then the container is checked in.
```python
with Broker(container_host="ch-d:rhel7") as container_host:
    assert container_host.hostname in container_host.execute("hostname").stdout
```


**Custom Host Classes**

You are encouraged to build upon the existing Host class broker provides, but need to include it as a base class for Broker to work with it properly. This will allow you to build upon the base functionality Broker already provides while incorporating logic specific to your use cases.
Once you have a new class, you can let broker know to use it during host construction.
```python
from broker import Broker
from broker.hosts import Host

class MyHost(Host):
   ...

with Broker(..., host_classes={'host': MyHost}) as my_host:
    ...
```


**Setup and Teardown**

Sometimes you might want to define some behavior to occur after a host is checked out but before Broker gives it to you. Alternatively, you may want to define teardown logic that happens right before a host is checked in.
When using the Broker context manager, Broker will run any `setup` or `teardown` method defined on the Host object.
Broker *will not* pass any arguments to the `setup` or `teardown` methods, so they must not accept arguments.
```python
<continued from above>
class MyHost(Host):
    ...
    def setup(self):
        self.register()

    def teardown(self):
        self.unregister()
```
**Note:** One important thing to keep in mind is that Broker will strip any non-pickleable attributes from Host objects when needed. If you encounter this, then it is best to construct your host classes in such a way that they can recover gracefully in these situations.
