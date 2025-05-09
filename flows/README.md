# Globus Flow Configuration for Batch

This folder includes details on how to create and run Flows for batch inference via uploaded files. It also includes configuration instructions for the Guest Collections for transfer and data sharing.

## Defining and Registering the Globus Flow

All of the details can be found in the `batch_flow.ipynb` Jupyter notebook.

## Setting the Transfer Endpoint on the API Host

### Installation
If you do not have a Globus Connect Server installed on the machine hosting the API (e.g. a VM), you can use a [Globus Connect Personal](https://docs.globus.org/globus-connect-personal/install/linux/) endpoint. First, create a directory and download the source code:
```bash
mkdir /home/webportal/globus_personal_connect/
cd /home/webportal/globus_personal_connect/
wget https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz
tar xzf globusconnectpersonal-latest.tgz
```

Configure the endpoint:
```bash
cd /home/webportal/globus_personal_connect/globusconnectpersonal-3.2.6
./globusconnectpersonal -setup
```

### Systemctl Service

Then, as a regular user who has sudo access, move the service file ino the `systemd` directory and give back the ownership to the `webportal` shared user:
```bash
sudo cp /home/webportal/inference-gateway/flows/globusconnectpersonal.service /etc/systemd/system/globusconnectpersonal.service
sudo chown webportal:webportal /etc/systemd/system/globusconnectpersonal.service
```

Enable the service with `systemctl`:
```bash
sudo systemctl daemon-reload
sudo systemctl enable globusconnectpersonal
```

Start, stop, and restart, and get the status of Globus Connect Personal with:
```bash
sudo systemctl start globusconnectpersonal
sudo systemctl stop globusconnectpersonal
sudo systemctl restart globusconnectpersonal
sudo systemctl status globusconnectpersonal
```

This will create a `Private Mapped Collection` from which you can create `Guest Collections`.

### Access and Permissions

Make sure only the relevant folder is exposed by the endpoint by adding the following in `/home/webportal/.globusonline/lta/config-paths`
```bash
/home/webportal/inference-gateway/uploaded_files,1,1
```
The first `1` is to allow sharing and create Guest Collections, and the second `1` is to give read-write access (the write access is necessary to let the Flow delete the original uploaded file after the transfer).

Go on the Globus WebUI, and look for your Mapped Collection in the [list of Collections](https://app.globus.org/collections?scope=administered-by-me) (search for the name of your collection). Click on your collection, and then click on the Collections tab. Click on `Add Guest Collection`, put `/home/webportal/inference-gateway/uploaded_files/` in the `Path` field, fill the rest of the form, and click on `Create Guess Collection`.

In the `Permissions` tab of the Guess Collection, click on `Add Permissions`. Give read and write access to the Globus Application that will run the Globus Flow. Read permission is to allow the transfer to the destination, and the write permission is to allow the flow to delete the uploaded file once the transfer is completed.

## Setting the Transfer Endpoint on the HPC's Filesystem

For this use case, we use the Globus Connect Server installed for the Eagle filesystem. Make sure you [created a Guest Collection](https://docs.alcf.anl.gov/data-management/acdc/eagle-data-sharing/#creating-a-guest-collection) specifically for hosting the uploaded-file transfers and to host the LLM computation results. At the base of your collection, make sure you create the two following folders: `batch_results/` and `uploaded_files/`.

### Access and Permissions

In your Guest Collection, in the `Roles` tab, assign the `Access Manager` role to the Globus Application that will run the Globus Flow. This will allow the application to 1) transfer files to the Guess Collection, and 2) assign user-base permissions to share inference results with users.


