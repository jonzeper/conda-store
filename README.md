# Conda Store

<a href="https://conda-store.readthedocs.io/en/latest/">
  <img src="https://readthedocs.org/projects/conda-store/badge/?version=latest" alt="Documentation Status"/>
</a>

<a href="https://pypi.org/project/conda-store-server/">
  <img src="https://img.shields.io/pypi/v/conda-store-server?label=pypi|conda-store-server" alt="PyPi"/>
</a>

<a href="https://pypi.org/project/conda-store/">
  <img src="https://img.shields.io/pypi/v/conda-store-server?label=pypi|conda-store" alt="PyPi"/>
</a>

<a href="https://anaconda.org/conda-forge/conda-store-server">
  <img src="https://img.shields.io/conda/vn/conda-forge/conda-store-server?color=green&label=conda-forge%7Cconda-store-server" alt="Conda"/>
</a>

<a href="https://anaconda.org/conda-forge/conda-store">
  <img src="https://img.shields.io/conda/vn/conda-forge/conda-store?color=green&label=conda-forge%7Cconda-store" alt="Conda"/>
</a>

![Conda-Store UI](docs/_static/images/conda-store-authenticated.png)

End users think in terms of environments not packages. The core
philosophy of Conda-Store is to serve identical Conda environments in
as many ways as possible. Conda-Store controls the environment
lifecycle: management, builds, and serving of environments.

It **manages** Conda environments by:
 - watching specific files or directories for changes in environment filename specifications 
 - provides a REST API for managing environments (which a JupyterLab plugin is being actively developed for)
 - provides a command line utility for interacting with conda-store `conda-store env [create, list]`
 - provides a web UI to take advantage of many of conda-store's advanced capabilities

It **builds** Conda specifications in a scalable manner using `N`
workers communicating via Celery to keep track of queued
environment builds.

It **serves** Conda environments via a filesystem, lockfile, tarball,
and a docker registry. Tarballs and Docker images can carry a lot of
bandwidth which is why Conda-Store integrates optionally with `s3` to
actually serve the blobs.

## Documentation

All documentation can be found on Read the Docs including how to develop
and contribute to the
project. [conda-store.readthedocs.io](https://conda-store.readthedocs.io).

## Terminology

 - A `namespace` is a way of scoping environments

 - An `environment` is a `namespace` and `name` pointing to a particular `build`

 - A `specification` is a Conda environment `yaml` declaration with fields `name`,
   `channels`, and `dependencies` as detailed
   [here](https://docs.conda.io/projects/conda-build/en/latest/resources/package-spec.html)
   
 - A `build` is a build (`conda env create -f <specification>`) of a
   particular `specification` at a point in time for a given `namespace`

This design has several advantages:
 - `environments` can be "rolled back" to a given `build` - not necessarily the latest
 - because each `environment` update is a new separate build the
   environment can be archived and uniquely identified

![Conda-Store terminology](docs/_static/images/conda-store-terminology.png)

## Philosophy

We mentioned above that `Conda-Store` was influenced by
[nix](https://nixos.org/). While Conda is not as pure as nix (when it
comes to reproducible builds) we can achieve close to the same results
with many of the great benefits. Motivation
from this work came from the following projects in no particular
order: [lorri](https://github.com/target/lorri), [nix layered docker
images](https://grahamc.com/blog/nix-and-layered-docker-images),
[nix](https://nixos.org/), [nixery](https://nixery.dev/). 

1. specifications are idempotent, created once, and never updated
   (this means there is no `conda install` or `conda env update`). In
   fact there is only one Conda command `conda env create -f
   <specification>`.
2. specifications are named
   `<sha256-hash-of-spec>-<environment-name>`, ensuring every Conda
   environment is unique.
3. a Conda environment for example `<environment-name>` is symlinked to a
   specific Conda specification
   `<sha256-hash-of-spec>-<environment-name>`.

The benefits of this approach are versioning of environments, heavy
caching, and rollbacks to previous environment states. 

## License

Conda-Store is [BSD-3 LICENSED](./LICENSE)

## Contributing

Our [documentation has all the information needed for
contributing](https://conda-store.readthedocs.io/en/latest/contributing.html). We
welcome all contributions.
