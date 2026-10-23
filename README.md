# CircuitNinja for KiCad

Manufacturing tools for KiCad PCB Editor, connected to
[CircuitNinja](https://circuitninja.com).

- **Set manufacturing rules** — apply CircuitNinja's manufacturing constraints to your project.
- **Check manufacturability** — run checks and navigate to findings in the editor.
- **Export manufacturing files** — generate a ZIP of fabrication files and reports compatible with CircuitNinja's web app.
- **Upload to web app** — upload the generated files and continue ordering in the browser. A CircuitNinja account is required for this.

The plugin uses both the older SWIG and the newer IPC API.

## Installation

### Repository URL

1. Open **Tools > Plugin and Content Manager** from KiCad's main window.
2. Click **Manage...** (Repositories), then **Add Existing** (shown as **+** button with **Add repository** tooltip).
3. Paste this repository URL, confirm with **OK**, then click **Save**:

   ```text
   https://raw.githubusercontent.com/CircuitNinjaPCB/kicad-plugin/main/releases/repository.json
   ```

4. Select **CircuitNinja for KiCad** in the repository dropdown. Find the **CircuitNinja for KiCad** package and click **Install**, then **Apply Pending Changes**. Wait for installation to finish.
5. Open **Preferences > Preferences > Plugins** from KiCad's main window, check **Enable KiCad API**, and confirm with **OK**. If a Python interpreter must be selected, use Python 3.10 or newer (the bundled Python on Windows, or the system Python on native Linux).
6. Close and reopen KiCad, then open your board in **PCB Editor**. Keep an internet connection available while KiCad prepares the plugin's Python environment and installs dependencies.
7. Click the **CircuitNinja shuriken icon** in the PCB Editor toolbar to open the menu. Separate toolbar buttons also open the manufacturing workflows directly.

### ZIP

1. Download `com.circuitninja.kicad-<version>.zip` from the [releases folder](https://github.com/CircuitNinjaPCB/kicad-plugin/tree/main/releases). Use the package ZIP, not `resources.zip` or GitHub's **Download ZIP** source archive. Do not extract it.
2. Open KiCad's **Plugin and Content Manager** and click **Install from File...**.
3. Select the downloaded package ZIP and click **Open**. Wait for installation to finish.
4. Follow steps **5–7** above to enable the API, restart KiCad, and open CircuitNinja. An internet connection is still needed for the initial Python dependency installation.

To update a manually installed package, download and install the newer package ZIP in the same way.

## Compatibility

- **Supported:** KiCad 9.0.9.1 and later 9.x releases, and KiCad 10.x, on Windows and native Linux installations.
- **Python:** 3.10 or newer is required for the plugin environment; KiCad manages its dependencies.
- **Not supported:** older KiCad 9 releases, KiCad 8 or earlier, KiCad 11 or later, and macOS.
- **Linux AppImages are not supported:** known Python environment setup problems can prevent the plugin from starting. Use a native KiCad installation instead.

## Troubleshooting

* On Debian/Ubuntu Linux, if the plugin windows fail to open with an `xcb` error, try installing `libxcb-cursor0` and restarting KiCad:

   ```bash
   sudo apt install libxcb-cursor0
   ```
