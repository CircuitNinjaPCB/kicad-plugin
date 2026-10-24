# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/2.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-10-24

### Fixed

- Added a clearer startup error for Qt `xcb` failures, with a suggested Debian/Ubuntu dependency installation command.
- Clarified repository installation instructions and documented Linux `libxcb-cursor0` troubleshooting in the README and KiCad package description.

## [1.0.0] - 2026-10-16

Initial public release with manufacturing rules configuration, manufacturability checks, manufacturing file export, and board uploads to the CircuitNinja web app. Includes browser-based authentication, organization selection, sign-out, and removal of saved client registration.
