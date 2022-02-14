History
=======

0.1.35 (2022-02-14)
------------------

+ Added basic support for AAP
+ Silenced urllib3 warnings for users who make questionable decisions

0.1.34 (2022-02-13)
------------------

+ Improved the reliability of Session.run

0.1.33 (2021-12-16)
------------------

+ Improved the resiliency of multiprocessing checkouts

0.1.32 (2021-12-07)
------------------

+ Added configurable ssh connection timeout

0.1.31 (2021-11-11)
------------------

+ Added a locking mechanism to actions that modify files

0.1.30 (2021-11-05)
------------------

+ Greatly speed up Session's sftp_write

0.1.29 (2021-11-03)
------------------

+ Actually include a line that was supposed to be in 0.1.28

0.1.28 (2021-11-03)
------------------

+ Suppress warnings on inventory load
+ Move file arg evaluation to VMBroker's init

0.1.27 (2021-10-29)
------------------

+ Made CLI checkouts properly raise exceptions

0.1.26 (2021-10-05)
------------------

+ Added retries to socket.connect
+ Added some better error handling when decoding stdout data

0.1.25 (2021-09-30)
------------------

+ Extends are now concurrent by default
+ Color codes are stripped from log files
+ Broker now respects its own debug setting
+ Session command timeouts are now flexible

0.1.24 (2021-09-16)
------------------

+ Add back in getattr call to Host methods

0.1.23 (2021-09-10)
------------------

+ Added interactive shell functionality to sessions
+ Small fix for closing sessions

0.1.22 (2021-07-16)
------------------

+ Handle checkin on instance with no hosts
+ Handle Pickle raising an AttributeError
+ Better error handling for AnsibleTower

0.1.21 (2021-07-01)
------------------

+ Improved concurrent checkin resilience
+ Increased AnsibleTower page size for workflows and job templates
+ GHA Updates to pypi and quay publishing

0.1.20 (2021-06-25)
------------------

+ Checkins run concurrently by default

0.1.19 (2021-06-18)
------------------

+ Host session objects are now a property
+ AnsibleTower will only display workflow and job templates
  a user had permission to start

0.1.18 (2021-05-27)
------------------

+ Added the ability to store important output from cli
+ Added Emitter class and emit instance to helpers
+ Added a way to gain failure information for AnsibleTower
+ Misc fixes and tweaks

0.1.17 (2021-05-10)
------------------

+ Added remote_copy to the Host's session object
+ Enhanced sync behavior for VMBroker and AnsibleTower

0.1.16 (2021-05-05)
------------------

+ AnsibleTower users can now specify a new expire vm time
+ minor fix to VMBroker context manager

0.1.15 (2021-04-23)
------------------

+ Added some enhahncements to the default Host class
+ It is now possible to define setup and teardown behavior
+ HostErrors now how their own exception class

0.1.14 (2021-04-15)
------------------

+ Allow VMBroker to checkin hosts not in its inventory
+ Add tower_inventory field when gathering host info

0.1.13 (2021-04-14)
------------------

+ AnsibleTower provider no longer requires a inventory specified.

0.1.12 (2021-04-13)
------------------

+ Minor changes for AnsibleTower provider

0.1.11 (2021-03-26)
------------------

+ Broker now handles ansible tower inventories
+ New exception handling system
+ New dynamic cli options for providers
+ Minor refactor and improvements

0.1.10 (2021-02-26)
------------------

+ Added the ability to pass in complex data structures via files

0.1.9 (2021-01-29)
------------------

+ Refactored broker's settings and dynaconf validator patterns
+ Added the concept of instances
+ Broker now passes along a host's stored _broker_args during checkin

0.1.8 (2021-01-21)
------------------

+ Quick fix for AnsibleTower provider

0.1.7 (2021-01-15)
------------------

+ Added the ability for broker to execute and query AnsibleTower job templates

0.1.6 (2020-12-08)
------------------

+ Broker now poulates missing field values based on returned results from AnsibleTower
+ AnsibleTower's artifacts strategy has been changed from merge to latest by default

0.1.5 (2020-10-31)
------------------

+ Added a list-templates ability for AnsibleTower provider
+ Added GNU license
+ minor fixes
- removed Michael

0.1.4 (2020-10-12)
------------------

+ --version flag added to main broker command
+ minor fixes

0.1.3 (2020-09-24)
------------------

+ HISTORY file format changed from rst to md
+ TestProvider now picklable, enabling mp test to run again

0.1.2 (2020-09-17)
------------------

+ nick-help command changed to providers
+ providers command is now dynamically populated
+ Fixes for logging
+ Now more resilient to running outside of broker's directory

0.1.1 (2020-09-02)
------------------

+ Settings values now have validation and some defaults
+ Filters are Introduced
+ VMBroker can now reconstruct hosts from the local inventory
+ Other miscellaneous enhancements and fixes

0.1.0 (2020-08-08)
------------------

+ VMBroker now has the ability to multiprocess checkouts
+ Other miscellaneous enhancements and fixes

0.0.12 (2020-07-31)
------------------

+ Added ability to extend vm lease time
+ Changed --artifacts to now accept merge or last
+ Misc small changes

0.0.11 (2020-07-02)
------------------

+ Added background mode to broker's cli
+ Added log-level silent

0.0.10 (2020-06-29)
------------------

+ Updated broker to be compatible with dynaconf 3.0.0
+ Added the ability to specify a BROKER_DIRECTORY envrionment variable
+ Changed settings.yaml to broker_settings.yaml

0.0.9 (2020-06-19)
------------------

+ Added inventory sync functionality to broker
+ Added the ability to query actions from providers using nick-help
+ Misc enhancements and tweaks including improving inventory host removal

0.0.8 (2020-06-03)
------------------

+ Added execute functionality to broker
+ Added more functionality to VMBroker subclass to handle execute
+ Slightly changed AnsibleTower provider to allow for arbitrary workflow execution

0.0.7 (2020-05-29)
------------------

+ Added session class
+ Added session functionality to Host class
+ Updated VMBroker context manager
+ Added ssh2-python dependency (requires cmake)
+ New host settings added to settings.yaml.example

0.0.6 (2020-05-27)
------------------

+ Added nick-help subcommand
+ Added new helper method for presenting complex data structures
+ Changed --debug to --log-level allowing for greater log control
+ Improved in-code documentation

0.0.5 (2020-05-14)
------------------

+ Added initial tests
+ Added travis integration
+ Added a helper method for tests
+ Minor fixes and tweaks

0.0.4 (2020-05-08)
------------------

+ Refactored location and process of checkin/checkout
+ Added a Test Provider ahead of adding tests

0.0.3 (2020-04-30)
------------------

+ Introduced duplicate command
  Note that this will not work with old inventory format

0.0.2 (2020-04-30)
------------------

+ Updated awxkit version
- Removed provider from host information

0.0.1 (2020-04-28)
------------------

+ Initial commit
+ Added basic featureset
