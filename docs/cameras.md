# Cameras

Cameras are machine-mounted (`cameras:`, with a fixed `location`) or head-mounted (`heads.<id>.cameras:`, with an
`offset` from the head). Every camera can be watched as an MJPEG stream in any browser, and the API gives
snapshots, device controls and a list of the video devices on the server.

| Type | Source | Status |
|---|---|---|
| `simulated` | Draws what a camera at its location would see | Works |
| `v4l2` | USB cameras through Video4Linux (OpenCV) | Works; tested with a video file and simulated device lists, bench test pending |

## Settings of every camera

| Setting | Meaning | Default |
|---|---|---|
| `looking` | `up` or `down` | required |
| `location` | Machine-mounted: where the camera looks (X, Y, and Z = the height in focus) | – |
| `offset` | Head-mounted: from the head's reference point | – |
| `axes` | Head-mounted: the camera's own `z` and `rotation` axes (usually virtual) | – |
| `units_per_pixel` | mm per pixel in X and Y, **after** the transform | – |
| `transform.crop` | `{x, y, width, height}`: the part of the sensor image to keep | none |
| `transform.rotate` | 0, 90, 180 or 270 degrees clockwise | 0 |
| `transform.flip_x` / `flip_y` | Mirror left-right / top-bottom | false |
| `idle_close` | Close the device this long after the last viewer left; 0 = keep it open once opened | 10000 ms |
| `jpeg_quality` | For streams and snapshots | 80 |
| `stream_fps` | Default frame rate of MJPEG streams (a client can ask for less) | 15 |

The transform is applied to every frame in this order: crop, rotate, flip. Vision (phase 6) will see the same
image as the stream.

## `simulated`

| Setting | Meaning | Default |
|---|---|---|
| `width`, `height` | Image size | 640 × 480 |
| `fps` | Frames per second | 10 |
| `scene` | `bed`: the bed under a down-looking camera: grid (1, 10, 50 mm), coordinates every 50 mm, a 0805 part at every feeder's pick location. `nozzles`: the nozzles over an up-looking camera, with a mark showing their rotation, blurred when out of focus. `test`: colour bars and a clock. `auto`: `bed` looking down, `nozzles` looking up | `auto` |
| `focus_depth` | Up-looking: how many mm away from `location.z` a nozzle can be before it blurs | 3 |

The images follow the machine. Moving the head moves the bed under the top camera, and moving a nozzle over the
bottom camera brings it into view. That makes jogging, offsets and (later) vision testable without hardware.

## `v4l2`

```yaml
cameras:
  bottom:
    type: v4l2
    looking: up
    device:                          # tried in this order; the first present device wins
      usb_serial: "20131107"
      usb_id: "0c45:6340"
      path: /dev/v4l/by-path/platform-xhci-hcd.0-usb-0:1.2:1.0-video-index0
    format: {width: 640, height: 480, fps: 30, pixel_format: YUYV}
    controls: {focus_automatic_continuous: false, focus_absolute: 250}
    location: {x: 189.9, y: 49.5, z: 38.0}
    units_per_pixel: {x: 0.02, y: 0.02}
```

| Setting | Meaning | Default |
|---|---|---|
| `device.usb_serial` | USB serial number: follows the camera to any port. Some cheap cameras share one serial (e.g. `SN0001`); combine with `usb_id` | – |
| `device.usb_id` | `vendor:product`: the first camera of that model | – |
| `device.name` | The name the driver reports (`USB 2.0 Camera`) | – |
| `device.path` | `/dev/videoN`, or a stable `/dev/v4l/by-path/...` (a USB port) or `/dev/v4l/by-id/...` link | – |
| `format` | `width`, `height`, `fps`, `pixel_format` (FourCC: `MJPG` uses far less USB bandwidth than `YUYV`) | required |
| `controls` | V4L2 controls set after opening, named as `v4l2-ctl --list-ctrls` shows them. Booleans and on/off are 1/0 | `{}` |
| `buffer_size` | Driver frame buffers; 1 = always the newest frame (no lag) | 1 |
| `watchdog.no_frame_timeout` | No frame for this long counts as a failure | 3000 ms |
| `watchdog.reopen` | After a failure, close and reopen the device (e.g. after a USB reset) | true |
| `watchdog.reopen_delay` | Wait before reopening | 1000 ms |

Notes:

- USB cameras show up as two nodes (`video0`, `video1`): index 0 is the picture, index 1 metadata. Only index 0 is
  matched.
- Controls are set with `v4l2-ctl` when it is installed (`sudo apt install v4l-utils`), which knows every control
  the camera has. Without it, the common ones (brightness, contrast, saturation, gain, exposure, focus, white
  balance, ...) go through OpenCV. Controls that can't be set are shown as the camera's `last_error`.
- The format actually delivered (cameras round to what they support) is in the camera's status, `actual`.
- Control names differ between kernels: newer ones say `focus_automatic_continuous` and `auto_exposure`, older ones
  `focus_auto` and `exposure_auto`. `camera.controls` lists the names this camera has.

## When the device is open

A camera opens its device when the first viewer arrives: a stream, a snapshot, or later vision. It closes
`idle_close` ms after the last viewer leaves. Several viewers share one capture, and each frame is JPEG-encoded
once per overlay combination. The status shows `viewers`, `streaming`, the measured `fps`, `width` and `height`.

## HTTP

| URL | Result |
|---|---|
| `/cameras/<id>/stream.mjpg` | Live MJPEG (`multipart/x-mixed-replace`); works in an `<img>` tag. `?fps=` (lower rate), `?overlay=`, `?frames=N` (stop after N frames) |
| `/cameras/<id>/snapshot.jpg` | One new frame. `?overlay=` |
| `/cameras/<id>/frames/<n>.jpg` | Frame `n` from `camera.snapshot`; the last 10 frames are kept |

Overlays are drawn on the server, so every front end gets them: `crosshair`, `scale` (a bar of a round length, from
`units_per_pixel`), `grid` (lines at a round mm spacing), `info` (camera id, location, size). Combine them with
commas: `?overlay=crosshair,scale`.

## API

`camera.list`, `camera.snapshot`, `camera.devices`, `camera.controls`, `camera.set_controls`, `camera.reopen`: see
[`api.md`](api.md), section 9.

## Not yet

Lens calibration (undistort), automatic `units_per_pixel` measurement, camera calibration helpers (phase 2) and
vision (phase 6). Other sources (Raspberry Pi camera through libcamera, network streams) can be added as camera
types; OpenCV already opens most network streams, so a `url` camera type would be small.
