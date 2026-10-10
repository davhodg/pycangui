# Code signing policy

**Windows releases are not code-signed at present.** Windows SmartScreen warns about the
installer when it is downloaded and first run, and *More info > Run anyway* goes past it.
Where an unsigned program is a problem, install from [PyPI](https://pypi.org/project/pycangui/)
with `pip install pycangui` instead: SmartScreen does not apply to it.

Signing is intended, and how it will be done is not yet settled. This page says what will be
signed and who is responsible for it, and will say how, and from which release, once that is
decided.

## What will be signed

The Windows build attached to each [release](https://github.com/davhodg/pycangui/releases):

- `pycangui.exe`, the application, signed before it is packaged; and
- `pycangui-<version>-setup.exe`, the installer that carries it.

Both are built by this repository's GitHub Actions workflow
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) from the tagged commit. Only what
that workflow built from a tagged commit will be signed: nothing built anywhere else.

Meanwhile every release installer carries a **build attestation**: GitHub signs, with
[Sigstore](https://www.sigstore.dev/), a record that the file was built by this workflow from
the tagged commit, and keeps it in a public log. To check a download:

```
gh attestation verify pycangui-<version>-setup.exe --repo davhodg/pycangui
```

That says where a file came from. It is not an Authenticode signature, so Windows does not read
it and SmartScreen still warns.

The other files in the build -- Python, Qt and the libraries pycangui uses -- are other
projects' work and are not signed by this project; they are listed in
`THIRD-PARTY-NOTICES.txt`. The packages on [PyPI](https://pypi.org/project/pycangui/) are not
code-signed; PyPI carries attestations of the workflow that built them instead.

## Team roles

pycangui has one maintainer, who holds every role, and uses multi-factor authentication for
the repository on GitHub, as will be used for whatever does the signing:

| Role | Members |
|---|---|
| Authors -- write the source | [davhodg](https://github.com/davhodg) |
| Committers -- can change this repository | [davhodg](https://github.com/davhodg) |
| Reviewers -- review every change from someone else before it is merged | [davhodg](https://github.com/davhodg) |
| Approvers -- approve each signing request | [davhodg](https://github.com/davhodg) |

Contributions from anyone else come as pull requests, reviewed before they are merged; see
[CONTRIBUTING.md](CONTRIBUTING.md), and the [code of conduct](CODE_OF_CONDUCT.md). A release is
made by the maintainer pushing a version tag, and once releases are signed, each signing
request it raises is approved by an approver before anything is signed.

## Privacy policy

This program will not transfer any information to other networked systems unless specifically
requested by the user or the person installing or operating it.

In practice that means three things, each asked for: *Help > Check for updates* (which asks
GitHub, or PyPI for a pip installation, for the latest version), opening a web page you chose,
and installing the optional MDF reader from PyPI after you agree to it. [SECURITY.md](SECURITY.md)
lists them too, and treats pycangui going online when you did not ask it to as a fault to
report.

pycangui talks to CAN adapters and the equipment on the bus, which is what it is for; a bus is
not a networked system in this sense, and nothing read from one is sent anywhere else.

The services those three contact have privacy policies of their own: GitHub's
[Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement)
for checking for updates to the installed build or a source folder, and the Python Software
Foundation's [Privacy Notice](https://policies.python.org/python.org/Privacy-Notice/) for PyPI,
for checking for updates to a pip installation and for installing the MDF reader.

## Changes to the system, and uninstalling

The installer says what it will do before it does it: a desktop shortcut and opening `.dcf`
and `.eds` files with pycangui are tasks shown, and can be unticked, before anything is
installed, and pycangui is only added to *Open with* -- the default is left alone. It
installs an uninstaller, which takes away the program, its shortcuts and its file
associations.

Inside pycangui, a Start menu entry and opening `.dcf` and `.eds` files are added only from
*Tools > Settings*, when asked for. The [README](README.md#uninstalling) says how to uninstall
each way of installing pycangui, those two included. Your own files -- hooks, EDS files,
settings -- are never removed by uninstalling, and the README says where they are.
