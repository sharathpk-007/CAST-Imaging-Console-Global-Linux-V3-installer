# User Guide: CAST Imaging V3 Global Linux Installer

**1. Introduction**

Welcome to the CAST Imaging V3 Global Linux Installer. This application provides a graphical user
interface on a Windows machine to automate the installation and management of CAST Imaging V
on remote Linux servers. It supports both single-machine and distributed deployments, handles
prerequisite installations, and simplifies configuration and cleanup tasks.

**Key Features:**

- Guided, step-by-step installation workflow.
- Support for both **Single Machine** and **Distributed** installation modes.
- Automated **Docker Engine** installation for various Linux families.
- Secure, non-interactive handling of sudo privileges.
- Intelligent local caching of the installation package to save time and bandwidth.
- A "batch screen" for real-time task status and detailed command logs.
- Integrated tools for uninstalling and cleaning up deployments.
- Comprehensive logging to a local file for troubleshooting, with an optional debug mode.
**2. System Prerequisites**
- **Windows Machine (Controller):**
- **Linux Server(s) (Target):**

```
o A compatible Linux distribution (e.g., Ubuntu, Debian, CentOS, RHEL).
o SSH access enabled, with credentials (username, password, and sudo password) for a
user with sudo privileges.
```
**3. Initial Setup**

Before performing any actions, you must configure the initial setup parameters.

1. **CAST Extend API Key:** Enter your personal API key for the CAST Extend platform. This is
    required to download the installation package. You can obtain this key from your profile
    settings on the CAST Extend website.


2. **Version:** Specify the version of CAST Imaging you wish to install. Use latest to automatically
    get the most recent release, or enter a specific version number (e.g., 3.3.0).
3. **Linux OS Family:** Select the operating system family of your target Linux server(s) from the
    dropdown menu. This ensures the correct commands are used for installing prerequisites like
    Docker.
4. **Installation Mode:**

```
o Choose Single Machine if you intend to install all CAST Imaging components on one
Linux server.
o Choose Distributed if you will be installing different components on different
servers.
```
5. **Log File Path:** This field shows where the detailed log file for all operations will be saved on
    your Windows machine. You can edit the path directly or use the **Browse...** button to choose
    a new location.
6. **Enable Debug Logging:** Check this box if you encounter issues and need to generate a highly
    detailed log file for troubleshooting. This will include verbose output from the underlying
    SSH library.


**4. Server Details**

Based on the **Installation Mode** you selected, you will need to provide the SSH connection details for
the target server(s).

**For Single machine Mode:**

- **Host:** The IP address or hostname of the Linux server.
- **Port:** The SSH port (defaults to 22).
- **Username:** The username for the SSH connection (e.g., root, ubuntu).
- **Password:** The password for the specified username.
- **Sudo Password:** The password for the specified username to execute sudo commands. This
    is mandatory for installation and clean-up tasks. Click the **" i "** button for more information.


**For Distributed Mode:**

- Fill in the details for each required component host. You can use the same host details for
    multiple components if they are co-located.
- Use the **Add Analysis Node** button to add connection details for one or more analysis node
    servers.

**Test Connection:**
Use the **Test Connection** button next to any server entry to verify that the application can
successfully connect with the provided credentials. A success or failure pop-up will appear.


**5. Actions Workflow**

The application is designed to be used in a sequential, step-by-step workflow.

**Step 1: Install Docker Engine (Optional)**

This step ensures that Docker and Docker Compose are installed on all target servers. If you are
certain they are already installed and correctly configured, you may skip this.

1. Click the **" i "** button to review the Docker requirements.
2. Click the **Install Docker Engine** button.
3. The application will connect to each server defined in the "Server Details" section and run
    the appropriate installation commands for the selected OS family.
4. Monitor the **Task Status** window for progress and the **Detailed Command Output** window
    for the raw script output.
5. After completion, a pop-up will appear. **A reboot or re-login on the Linux server(s) is**
    **required** for the user's new Docker permissions to take effect.

**Note: If any failure during the installation process of Docker Engine, it could be that the Linux OS is
not correctly selected or is not compatible with this installation.
You can directly install the Docker engine from the official site in all the Linux servers.**
https://docs.docker.com/engine/install/


**Step 2: Prepare Server(s)**

This step downloads the CAST Imaging package to your Windows PC and then uploads and unzips it
on the target Linux server(s).

1. Click the **Prepare Server(s)** button.
2. **If the installation package already exists** on your PC from a previous run, a pop-up will ask if
    you want to use the existing file. This saves time if you are re-running the process on a new
    server. Choose "No" to re-download a fresh copy.
3. Monitor the **progress bar** and **Task Status** window. The process involves:
    o Downloading the package to your local machine (if needed).

```
o Checking if the target directory ~/Cast-Imaging already exists on the remote server. If
it does, the upload and unzip for that server will be skipped.
o Uploading the package to each server.
```
```
o Unzipping the package on each server.
```
4. Once complete, the "Fetch Config" button will be enabled.


**Step 3: Configuration File Editor**

This step allows you to fetch, edit, and upload the configuration.conf file.

1. **Fetch Config:** Click this button to download the configuration.conf file from the primary
    server and display its contents in the text editor.
2. **Edit:** Make any necessary changes to the configuration directly in the text box.
3. **Upload Config:** Click this button to save your changes and upload the
    modified configuration.conf file back to **all** specified servers. This ensures consistency in a
    distributed setup.
4. Once the upload is successful, the "Run CAST Installation" button will be enabled.

**Step 4: Run CAST Installation**

This is the final installation step that executes the CAST Imaging scripts on the remote servers.


1. Click the **Run CAST Installation** button.
2. The application will connect to the server(s) and run the necessary commands non-
    interactively, using the Sudo Password you provided.
       o It will first clean the configuration.conf file of any Windows-specific characters.

```
o It will set executable permissions (chmod).
o It will run the appropriate install script (all for single-machine, or a specific
component for distributed).
```
3. Monitor the log windows for progress. Upon completion, a success message will appear. You
    can now verify the status of the Docker containers on the Imaging Console V3 Linux widget.
**6. Uninstall / Clean-up**

This section provides tools to remove installations. These actions are powerful and should be used
with caution.

- **Selective CAST Imaging Clean-up:** This is the safer option. It stops only the CAST-specific
    Docker containers and removes the ~/Cast-Imaging installation directory. It does not affect
    other Docker containers or data on the host.
- **Complete Docker Clean-up: Use with extreme caution.** This is a destructive action that will
    delete **ALL** Docker containers, images, volumes, and networks on the target server(s), not
    just those related to CAST Imaging. A confirmation pop-up will appear before this action
    proceeds.


