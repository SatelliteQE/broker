# Functional Tests
These tests require their corresponding providers are setup and configured in order to run. You will also need to install the corresponding Broker functionality.
Due to the live config requirement, these tests are not run within Broker's github actions.
Do not attempt to use Broker while running these functional tests or you may end up with resources being checked in during the cleanup phases.

**Container Tests**

Setup:
- Ensure either Docker or Podman are installed and configured either locally or on a remote host.
- Ensure Broker's Container provider is configured with the details of the previous step.
- Clone the [content-host-d](https://github.com/JacobCallahan/content-host-d) repository and build the UBI8 image, tagging it as `ch-d:ubi8`.

**SatLab Tests**

Setup:
- Ensure you have your account credentials entered into the AnsibleTower provider section of Broker's config.
- Make sure you have room for at least 4 hosts in your current SLA limit.

Note: These tests take a while to run, up to around 45m.
