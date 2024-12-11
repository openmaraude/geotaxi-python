# Production

The infrastructure behind api.taxi is hosted on CleverCloud, where it is unfortunately not possible to host UDP applications. As a result, geotaxi is the only component hosted on a virtual machine from Online.net.

To deploy a new version of geotaxi:

* run `make tag` and `git push`
* wait until [circle-ci/config.yml](circle-ci/config.yml) publishes the Docker image [openmaraude/geotaxi-python](https://hub.docker.com/r/openmaraude/geotaxi-python)
* connect to geotaxi.api.taxi: `ssh -l root geotaxi.api.taxi`
* run `/root/redeploy-dev.sh` or `/root/redeploy-prod.sh`:

The containers `geotaxi` and `geotaxi-dev` listen on the IP addresses behind `geoloc.api.taxi` and `geoloc.dev.api.taxi`, as configured on our DNS records.

The Docker server must be installed via the system package manager.

IP failover must be configured to restore the historical IP addresses used by the late dedicated servers, and have the Docker containers listening on them.
See `01-netcfg.yaml` for an example on Ubuntu.
