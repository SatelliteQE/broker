[![PythonPackage](https://github.com/SatelliteQE/broker/actions/workflows/python-publish.yml/badge.svg)](https://github.com/SatelliteQE/broker/actions/workflows/python-publish.yml)
[![ContainerImage](https://github.com/SatelliteQE/broker/actions/workflows/update_broker_image.yml/badge.svg)](https://github.com/SatelliteQE/broker/actions/workflows/update_broker_image.yml)
[![CodeQL](https://github.com/SatelliteQE/broker/actions/workflows/codeql-analysis.yml/badge.svg)](https://github.com/SatelliteQE/broker/actions/workflows/codeql-analysis.yml)
# Broker
The infrastrucure middleman

# Description
Broker is a tool designed to provide a common interface between one or many services that provision virtual machines or containers. It is an abstraction layer that allows you to ignore most of the implementation details and just get what you need.

# Docs
Broker's docs can be found at the wiki for this repo: https://github.com/SatelliteQE/broker/wiki

# Quickstart
Install cmake with `dnf install cmake`

**Note:** We recommend using [uv](https://github.com/astral-sh/uv?tab=readme-ov-file#installation) to manage your Broker installation.

Install Broker either as a tool with uv `uv tool install broker` 

or with pip `pip install broker`

**Note:** If you install with pip it is recommended that you do so in a virtual environment.

(optional) If you are using the Container provider, install the extra dependency based on your container runtime of choice with either `... install broker[podman]` or `... install broker[docker]`.

(optional) If you are using the Beaker provider, install the extra dependency with `dnf install krb5-devel` and then `... install broker[beaker]`.

The first time you run Broker, like with `broker --version`, it will check if you already have a `broker_settings.yaml` in the location it expects.
If not, then it will help you get one setup and place it in the default broker directory `~/.broker/`

If you want Broker to operate out of a different location, export a `BROKER_DIRECTORY` environment variable with the desired path.

You can check `broker --version` at any time to verify where it is looking for its config file.

# Basic CLI Usage
**Checking out a VM or container**
To checkout a single VM with arbitrary arguments:
```
broker checkout --workflow test-workflow --workflow-arg1 something --workflow-arg2 else
```

To checkout multiple VMs at once:
```
broker checkout --workflow test-workflow --count 3
```

To pass complex data structures:
```
broker checkout --container-host my-image --args-file tests/data/broker_args.json --extra tests/data/args_file.yaml
```

**Nicks**

Broker allows you to define configurable nicknames for checking out vms. Just add yours to setting.yaml and call with the `--nick` option
```
broker checkout --nick rhel7
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
To sync an inventory for a specific instance, use the following syntax with --sync.
```
broker inventory --sync Container::<instance name>
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

You can also return a VM to its provider with the `checkin` command.
Containers checked in this way will be fully deleted regardless of its status.
You may use either the local id (`broker inventory`), the hostname, or "all" to checkin everything.
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
broker providers AnsibleTower --workflow test-workflow
```

**Run arbitrary actions**

If a provider action doesn't result in a host creation/removal, Broker allows you to execute that action as well. There are a few output options available as well.
When executing with the Container provider, a new container will be spun up with your command (if specified), ran, and cleaned up.
```
broker execute --help
broker execute --workflow my-awesome-workflow --additional-arg True
broker execute -o raw --workflow my-awesome-workflow --additional-arg True --artifacts last
```

**Machine processable output**

If running in a CI or other automated environment, Broker offers the choice to store important output information in an output file. This is json-formatted data. Please be aware that any existing file with the matching path and name will be erased.
```
broker --output-file output.json checkout --nick rhel7
broker --output-file inventory.json inventory
```

**Run Broker in the background**

Certain Broker actions can be run in the background, these currently are: checkout, checkin, and execute. When running a command in this mode, it will spin up a new Broker process and no longer log to stderr. To check progress, you can still follow broker's log file.
Note that background mode will interfere with output options for execute since it won't be able to print to stdout. Those should kept in log mode.
```
broker checkout --background --nick rhel7
broker checkin -b --all
broker execute -b --workflow my-awesome-workflow --artifacts
```

# Development Setup
Install cmake with `dnf install cmake`

Clone the Broker repository and install locally with  `uv pip install "broker[dev] @ ."`

Copy the example settings file to `broker_settings.yaml` and edit it.

To run Broker outside of its base directory, specify the directory with the `BROKER_DIRECTORY` environment variable.
