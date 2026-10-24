<h1 align="center">PlutoSiege</h1>

<p align="center">
  <strong>ISM-Band RF Capture & Replay Tool for PlutoSDR</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/License-GPL--3.0-blueviolet.svg?style=for-the-badge" alt="License">
  <img src="https://img.shields.io/badge/Hardware-ADALM--Pluto-0099FF?style=for-the-badge" alt="Hardware">
  <img src="https://img.shields.io/badge/Format-SigMF-orange?style=for-the-badge" alt="SigMF">
</p>

<p align="center">
  <img src="https://github.com/user-attachments/assets/967ccd8d-13ce-4795-80d5-c4c49010d6ba" alt="PlutoSiege Screenshot" width="100%">
</p>

> **REGULATORY DISCLAIMER**  
> RF transmission is regulated by local authorities. Replay only signals you own or are permitted to transmit, into a dummy load or at the lowest power required.

## About

**PlutoSiege** is a terminal application (TUI) for recording and replaying short ISM-band RF bursts (e.g. 433 MHz, 868 MHz) from car remotes, garage doors, doorbells, and wireless sensors.

Built for the **ADALM-Pluto (AD9363/AD9361)** SDR architecture, it saves recordings in standard **SigMF (Signal Metadata Format)** so they can be opened in other SDR tools like URH, IQEngine, GNU Radio, or Inspectrum.

### Hardware Compatibility
Fully supports both official **Analog Devices ADALM-Pluto** hardware (Rev. B / Rev. C) and third-party clones/analogues based on the same AD9363/AD9361 transceivers (such as **Pluto Nano**, **Pluto Plus**, and other custom PlutoSDR boards).

## Features

- **Level Metering**: Real-time dBFS level meter with saturation (`SAT!`) warning.
- **Threshold Detection**: Automated noise floor estimation or manual threshold setting.
- **Pre-Trigger Buffer**: Keeps a ring buffer of history before trigger so preambles are not missed.
- **SigMF Format**: Saves `.sigmf-meta` and `.sigmf-data` pairs.
- **GPS Geotagging**: Records precise coordinates in SigMF metadata via `gpsd` and displays geotags during replay.
- **Digital Loopback Test**: Built-in test screen to check hardware loopback and SNR.
- **Linux Native**: Built and optimized specifically for Linux environments (supports all major distribution families: Debian-based, RPM-based, Arch-based, etc.).

## Installation

### 1. System Drivers (libiio)
Install the required IIO system drivers for your distribution family:

**Debian-based (Ubuntu, Debian, Kali, Mint, etc.):**
```bash
sudo apt update && sudo apt install -y libiio0 libiio-dev libiio-utils
```

**RPM-based (Fedora, RHEL, CentOS, Rocky, etc.):**
```bash
sudo dnf install -y libiio python3-iio libiio-utils
```

**Arch-based (Arch Linux, Manjaro, etc.):**
```bash
sudo pacman -S libiio
```

### 2. Optional: GPS Support (gpsd)
If you plan to use GPS geotagging for SigMF recordings, install the `gpsd` daemon and tools:

**Debian-based:**
```bash
sudo apt install -y gpsd gpsd-clients
```

**RPM-based:**
```bash
sudo dnf install -y gpsd gpsd-clients
```

**Arch-based:**
```bash
sudo pacman -S gpsd
```

> **Note:** Configure and start the `gpsd` service according to your specific GPS receiver hardware (USB dongle, UART module, Bluetooth, or virtual source) so that it listens on `127.0.0.1:2947`. Once running, enable GPS in PlutoSiege **Settings** (`Enable GPS (gpsd): yes`).

### 3. Python Package & Setup
Clone the repository and install dependencies:
```bash
git clone https://github.com/SketchTurnerDev/pluto_siege.git
cd pluto_siege
pip install -r requirements.txt
```

## Quick Start

Launch via the main script:
```bash
python pluto_siege.py
```

Or run as a package:
```bash
python -m pluto_siege
```

Or install in editable mode:
```bash
pip install -e .
pluto_siege
```

## Demo

<p align="center">
  <img src="https://github.com/user-attachments/assets/c3258d0d-f574-4a01-9853-89c5cb2afc33" alt="PlutoSiege Demo" width="100%">
</p>

## Application Menu

The main menu provides access to all core operations:

### 1. Capture
Listens for incoming RF bursts on the configured receiver frequency (`rx_freq`).
- Real-time dBFS level meter with saturation (`SAT!`) warnings.
- Automated noise floor estimation or manual threshold detection.
- Pre-trigger history ring buffer to preserve signal preambles.
- Saves captures as standard SigMF (`.sigmf-meta` and `.sigmf-data`) pairs with optional GPS geolocation tags.

### 2. Transmit
Browse and replay saved SigMF recordings.
- Displays recorded timestamp, center frequency, sample rate, sample count, and GPS coordinates.
- Replays signals via the PlutoSDR transmitter on the configured frequency (`tx_freq`).
- In-place deletion of unwanted recordings (`D` or `Delete`).

### 3. Loopback Test
Internal hardware diagnostic to verify the SDR transceiver and USB/DMA pipeline.
- Transmits an internal digital test tone through the AD936x loopback path.
- Calculates received Signal-to-Noise Ratio (SNR) to confirm hardware health.

### 4. Settings
Configures radio parameters, trigger behavior, GPS integration, and interface preferences. Settings can be edited directly in the TUI and are saved to `settings.json`.

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `pluto_uri` | `str` | `usb:` | Pluto connection URI (`usb:` or `ip:`). |
| `pluto_ip` | `str` | `192.168.2.1` | Remembered Pluto IP address when toggling between USB and IP modes. |
| `rx_freq` | `int` | `433920000` | Receiver center frequency in Hz. |
| `tx_freq` | `int` | `433920000` | Transmitter center frequency in Hz. |
| `permit_out_of_spec_frequency` | `bool` | `false` | Allows tuning outside 325 MHz – 3.8 GHz range (up to 70 MHz – 6.0 GHz). |
| `sample_rate` | `int` | `2100000` | Sample rate in Hz (521 kSPS – 61.44 MSPS). |
| `rx_gain` | `float` | `15.0` | Manual RX hardware gain in dB (0.0 to 74.5 dB). |
| `tx_gain` | `float` | `0.0` | Manual TX attenuation gain in dB (-89.75 to 0.0 dB). |
| `auto_threshold` | `bool` | `true` | Automated noise floor calculation. |
| `auto_trigger_margin` | `float` | `8.0` | Margin in dB above noise floor required to trigger capture (1.0 to 30.0 dB). |
| `manual_threshold` | `float` | `-30.0` | Fixed trigger threshold in dBFS when auto is off. |
| `pre_trigger_buffers` | `int` | `2` | Number of pre-trigger buffers kept in memory. |
| `silence_seconds` | `float` | `0.80` | Silence required to stop recording. |
| `max_post_trigger_seconds` | `float` | `4.00` | Maximum recording length after trigger. |
| `rx_buffer_size` | `int` | `32768` | DMA buffer size in samples (1024 to 32768). |
| `enable_gps` | `bool` | `false` | Hardware GPS geolocation in SigMF recordings via gpsd daemon. |
| `gpsd_host` | `str` | `127.0.0.1` | Local or remote gpsd daemon host address. |
| `gpsd_port` | `int` | `2947` | gpsd daemon TCP port (default 2947). |
| `enable_sound` | `bool` | `true` | Acoustic cue feedback for trigger, TX, and status events. |
| `enable_mouse` | `bool` | `true` | Terminal mouse interaction support. |

## License

Distributed under the **GNU General Public License v3.0** (GPL-3.0). See [`LICENSE`](LICENSE) for details.
