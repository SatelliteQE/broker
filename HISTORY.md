History
=======

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