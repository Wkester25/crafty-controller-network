[![Crafty Logo](app/frontend/static/assets/images/logo_long.svg)](https://craftycontrol.com)
# Crafty Controller 4.11.0
> Python based Control Panel for your Minecraft Server

## About this fork
An unofficial fork of [Crafty Controller](https://gitlab.com/crafty-controller/crafty-4)
that adds a **Network** page (Panel Config → Network):

- **UPnP port forwarding** for each server's ports, optionally only while it runs.
- **Cloudflare DNS**: each server gets its own subdomain (A record, plus an SRV
  record for Minecraft Java so players don't type a port), kept up to date if
  your IP changes.
- **Cloudflare Tunnel** for the web UI on its own subdomain, with Crafty
  running and supervising `cloudflared`.

Official releases are merged in automatically every week when the tests pass
([sync workflow](.github/workflows/sync-upstream.yml)).

### Install on Proxmox
Community-scripts style, on the **Proxmox host**:
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Wkester25/crafty-controller-network/main/ct/crafty-controller-network.sh)"
```
Update later by running `update` inside the container.

Or in any Debian 12/13 / Ubuntu 24.04 container or VM, as root:
```bash
curl -fsSL https://raw.githubusercontent.com/Wkester25/crafty-controller-network/main/proxmox/install.sh | bash
```
Run the same command again to update. It also converts a container made with
the official community `crafty-controller` script, keeping your servers.

## What is Crafty Controller?
Crafty Controller is a Minecraft Server Control Panel / Launcher. The purpose
of Crafty Controller is to launch a Minecraft Server in the background and present
a web interface for the server administrators to interact with their servers. Crafty
is compatible with Docker, Linux, Windows.

## Documentation
Documentation available on [Crafty Docs](https://docs.craftycontrol.com)

## Meta
Project Homepage - https://craftycontrol.com

Discord Server - https://discord.gg/9VJPhCE

Git Repository - https://gitlab.com/crafty-controller/crafty-4

Docker Hub - [arcadiatechnology/crafty-4](https://hub.docker.com/r/arcadiatechnology/crafty-4)

<br>

## Basic Docker Usage 🐳

With `Crafty Controller 4.0` we have focused on building our DevOps Principles, implementing build automation, and securing our containers, with the hopes of making our Container user's lives abit easier.

### - Two big changes you will notice is:
- We now provide pre-built images for you guys.
- Containers now run as non-root, using practices used by OpenShift & Kubernetes (root group perms).


> __**⚠ 🔻WARNING: [WSL/WSL2 | WINDOWS 11 | DOCKER DESKTOP]🔻**__ <br>
 BE ADVISED! Upstream is currently broken for Minecraft running on **Docker under WSL/WSL2, Windows 11 / DOCKER DESKTOP!** <br>
 On '**Stop**' or '**Restart**' of the MC Server, there is a 90% chance the World's Chunks will be shredded irreparably! <br>
 Please only run Docker on Linux, If you are using Windows we have a portable installs found here: [Latest-Stable](https://gitlab.com/crafty-controller/crafty-4/-/releases), [Latest-Development](https://gitlab.com/crafty-controller/crafty-4/-/jobs/artifacts/dev/download?job=win-dev-build)

----

### - To get started with docker 🛫
All you need to do is pull the image from this git repository's registry.
This is done by using `'docker-compose'` or `'docker run'` (You don't need to clone the Repository and build, like in 3.x ).

If you have a config folder already from previous local installation or _docker setup_*, the image should mount this volume and fix the permission as required, if no config present then it will populate its own config folder for you. <br> <br>
As the Dockerfile uses the permission structure of `crafty:root` **internally** there is no need to worry about matching the `UID` or `GID` on the host system :)

<br>

### - Using the registry image 🌎
The provided image supports both `arm64` and `amd64` out the box, if you have issues though you can build it yourself with the `compose` file in `docker/`.

The image is located at: `registry.gitlab.com/crafty-controller/crafty-4:latest` or `arcadiatechnology/crafty-4`
| Branch             | Status                                                                |
| ----------------- | ------------------------------------------------------------------ |
| :latest | [![pipeline status](https://gitlab.com/crafty-controller/crafty-4/badges/master/pipeline.svg)](https://gitlab.com/crafty-controller/crafty-4/-/commits/master) |
| :dev | [![pipeline status](https://gitlab.com/crafty-controller/crafty-4/badges/dev/pipeline.svg)](https://gitlab.com/crafty-controller/crafty-4/-/commits/dev)

<br>

**Here are some example methods for getting started🚀:**

### **docker-compose.yml:**
```sh
# Make your compose file
$ vim docker-compose.yml
```
```yml
services:
  crafty:
    container_name: crafty_container
    image: registry.gitlab.com/crafty-controller/crafty-4:latest
    restart: always
    environment:
      - TZ=Etc/UTC
    ports:
      - "5520-5550:5520-5550/udp" # Hytale
      - "8000:8000" # HTTP
      - "8443:8443" # HTTPS
      - "8123:8123" # DYNMAP
      - "19132:19132/udp" # BEDROCK
      - "25500-25600:25500-25600" # MC SERV PORT RANGE
    volumes:
      - ./docker/backups:/crafty/backups
      - ./docker/logs:/crafty/logs
      - ./docker/servers:/crafty/servers
      - ./docker/config:/crafty/app/config
      - ./docker/import:/crafty/import
```
```sh
$ docker-compose up -d && docker-compose logs -f
```
<br>

### **docker run:**
```sh
$ docker run \
	--name crafty_container \
	--detach \
	--restart always \
	-P 5520-5550:5520-5550/udp \
	-p 8000:8000 \
	-p 8443:8443 \
	-p 8123:8123 \
	-p 19132:19132/udp \
	-p 25500-25600:25500-25600 \
	-e TZ=Etc/UTC \
	-v "/$(pwd)/docker/backups:/crafty/backups" \
	-v "/$(pwd)/docker/logs:/crafty/logs" \
	-v "/$(pwd)/docker/servers:/crafty/servers" \
	-v "/$(pwd)/docker/config:/crafty/app/config" \
	-v "/$(pwd)/docker/import:/crafty/import" \
	registry.gitlab.com/crafty-controller/crafty-4:latest
```

### **Building from the cloned repository:**

If you are building from `docker-compose` you can find the compose file in `./docker/docker-compose.yml` just `cd` to the docker directory and `docker-compose up -d`

If you'd rather not use `docker-compose` you can use the following `docker run` in the directory where the *Dockerfile* is:
```sh
# REMEMBER, Build your image first!
$ docker build . -t crafty

$ docker run \
	--name crafty_container \
	--detach \
	--restart always \
	-P 5520-5550:5520-5550/udp \
	-p 8000:8000 \
	-p 8443:8443 \
	-p 8123:8123 \
	-p 19132:19132/udp \
	-p 25500-25600:25500-25600 \
	-e TZ=Etc/UTC \
	-v "/$(pwd)/docker/backups:/crafty/backups" \
	-v "/$(pwd)/docker/logs:/crafty/logs" \
	-v "/$(pwd)/docker/servers:/crafty/servers" \
	-v "/$(pwd)/docker/config:/crafty/app/config" \
	-v "/$(pwd)/docker/import:/crafty/import" \
	crafty
```
A fresh build will take several minutes depending on your system, but will be rapid thereafter.
