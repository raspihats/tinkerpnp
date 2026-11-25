// Hand-made settings pages per object type, as OpenPnP's Machine Setup has them: tabs of titled groups with labelled
// fields, and the buttons that measure a value next to it. Paths are relative to the object ("format.width").
// Defaults and help come from the type's JSON Schema where the layout doesn't give them. Anything a layout leaves
// out is still on the "All settings" tab.

export type Widget =
	| 'text'
	| 'number'
	| 'integer'
	| 'textarea'
	| 'select'
	| 'bool'
	| 'ref'
	| 'refs'
	| 'range'
	| 'list'
	| 'numbers'
	| 'json'
	| 'commands';

export type FieldSpec = {
	path: string;
	label: string;
	widget?: Widget;
	unit?: string;
	help?: string;
	/** select: the choices (value or [value, label]) */
	options?: (string | number | [string | number, string])[];
	/** ref / refs: the config section whose ids can be chosen */
	ref?: string;
	placeholder?: string;
	/** show only when this returns true */
	when?: (ctx: LayoutContext) => boolean;
	/** a narrow input (numbers in a row) */
	narrow?: boolean;
};

/** Buttons placed in a group: they measure or test something and fill fields. */
export type ActionId = 'device-picker' | 'capture-camera-location' | 'measure-units-per-pixel' | 'actuator-test' | 'head-mounted' | 'advanced-calibration' | 'tip-change';

export type GroupSpec = {
	title: string;
	note?: string;
	fields: FieldSpec[];
	/** fields in one row (x, y, z, rotation) */
	inline?: boolean;
	actions?: ActionId[];
	when?: (ctx: LayoutContext) => boolean;
};

export type TabSpec = {
	id: string;
	label: string;
	groups: GroupSpec[];
	when?: (ctx: LayoutContext) => boolean;
};

export type LayoutContext = {
	kind: string;
	type: string;
	path: string;
	value: Record<string, unknown>;
	/** a camera or nozzle on a head */
	onHead: boolean;
};

const name: FieldSpec = { path: 'name', label: 'Name', help: 'Shown in lists; the id stays what other settings refer to' };
const notes: FieldSpec = { path: 'notes', label: 'Notes', widget: 'textarea' };

function xyzr(base: string, withRotation = true, unit = 'mm'): FieldSpec[] {
	const f: FieldSpec[] = ['x', 'y', 'z'].map((a) => ({ path: `${base}.${a}`, label: a.toUpperCase(), widget: 'number', unit, narrow: true }));
	if (withRotation) f.push({ path: `${base}.rotation`, label: 'Rotation', widget: 'number', unit: '°', narrow: true });
	return f;
}

const isType = (...types: string[]) => (c: LayoutContext) => types.includes(c.type);

// ── cameras ──────────────────────────────────────────────────────────────────────────────────────────────────────
const camera: TabSpec[] = [
	{
		id: 'general',
		label: 'General',
		groups: [
			{
				title: 'Camera',
				fields: [
					name,
					{ path: 'looking', label: 'Looking', widget: 'select', options: [['up', 'Up: at the nozzles from below'], ['down', 'Down: at the bed']] }
				],
				actions: ['head-mounted']
			},
			{ title: 'Notes', fields: [notes] }
		]
	},
	{
		id: 'device',
		label: 'Device',
		when: isType('v4l2'),
		groups: [
			{
				title: 'Which camera',
				note: 'Pick a detected camera to fill this in. A USB serial is used only if no other camera has the same one; the USB port path stays the same while the camera stays in its port.',
				actions: ['device-picker'],
				fields: [
					{ path: 'device.usb_serial', label: 'USB serial' },
					{ path: 'device.usb_id', label: 'USB id', placeholder: 'vendor:product' },
					{ path: 'device.name', label: 'Device name' },
					{ path: 'device.path', label: 'Device path', placeholder: '/dev/v4l/by-path/…' }
				]
			},
			{
				title: 'Format',
				fields: [
					{ path: 'format.width', label: 'Width', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'format.height', label: 'Height', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'format.fps', label: 'Frame rate', widget: 'number', unit: 'fps', narrow: true },
					{ path: 'format.pixel_format', label: 'Pixel format', widget: 'select', options: ['MJPG', 'YUYV', 'H264'] }
				]
			},
			{
				title: 'Driver',
				fields: [
					{ path: 'buffer_size', label: 'Frame buffers', widget: 'integer' },
					{ path: 'controls', label: 'V4L2 controls', widget: 'json', help: 'Set after opening, e.g. {"focus_automatic_continuous": false, "focus_absolute": 300}' }
				]
			}
		]
	},
	{
		id: 'sim',
		label: 'Simulation',
		when: isType('simulated'),
		groups: [
			{
				title: 'Image',
				fields: [
					{ path: 'width', label: 'Width', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'height', label: 'Height', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'fps', label: 'Frame rate', widget: 'number', unit: 'fps', narrow: true },
					{ path: 'noise', label: 'Noise', widget: 'number' }
				]
			},
			{
				title: 'Scene',
				fields: [
					{ path: 'scene', label: 'Scene', widget: 'select' },
					{ path: 'focus_depth', label: 'Focus depth', widget: 'number', unit: 'mm' },
					{ path: 'sim_runout', label: 'Tip runout', widget: 'json' },
					{ path: 'sim_fiducials', label: 'Fiducials', widget: 'json' }
				]
			}
		]
	},
	{
		id: 'position',
		label: 'Position',
		groups: [
			{
				title: 'Location',
				note: 'Where the camera looks: for an up-looking camera, the nozzle centred over it and in focus.',
				when: (c) => !c.onHead,
				inline: true,
				fields: xyzr('location'),
				actions: ['capture-camera-location']
			},
			{
				title: 'Roaming radius',
				when: (c) => !c.onHead,
				fields: [
					{ path: 'roaming_radius', label: 'Roaming radius', widget: 'number', unit: 'mm', help: 'Shift+click in the view moves the nozzle at its depth within this distance of the location (OpenPnP)' }
				]
			},
			{
				title: 'Offset from the head',
				note: 'The camera centre relative to the head (the reference nozzle has offset 0).',
				when: (c) => c.onHead,
				inline: true,
				fields: xyzr('offset')
			},
			{
				title: 'Axes',
				note: 'A head camera moves with the head in X/Y; Z and rotation can be virtual axes.',
				when: (c) => c.onHead,
				fields: [
					{ path: 'axes.z', label: 'Z axis', widget: 'ref', ref: 'axes' },
					{ path: 'axes.rotation', label: 'Rotation axis', widget: 'ref', ref: 'axes' }
				]
			}
		]
	},
	{
		id: 'scale',
		label: 'Units per pixel',
		groups: [
			{
				title: 'Units per pixel',
				note: 'mm per pixel at the focus plane, after the image transforms.',
				inline: true,
				fields: [
					{ path: 'units_per_pixel.x', label: 'X', widget: 'number', unit: 'mm/px', narrow: true },
					{ path: 'units_per_pixel.y', label: 'Y', widget: 'number', unit: 'mm/px', narrow: true }
				],
				actions: ['measure-units-per-pixel']
			},
			{
				title: 'At a second height (3D)',
				note: "As OpenPnP's 3D units per pixel: mm/px measured at another Z; vision interpolates for the Z of what it looks at (e.g. fiducials on the board). Not needed with advanced calibration on.",
				inline: true,
				fields: [
					{ path: 'units_per_pixel_3d.primary_z', label: 'mm/px above holds at Z', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'units_per_pixel_3d.secondary_z', label: 'Second Z', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'units_per_pixel_3d.secondary.x', label: 'X there', widget: 'number', unit: 'mm/px', narrow: true },
					{ path: 'units_per_pixel_3d.secondary.y', label: 'Y there', widget: 'number', unit: 'mm/px', narrow: true }
				]
			}
		]
	},
	{
		id: 'calibration',
		label: 'Advanced calibration',
		groups: [
			{
				title: 'Advanced calibration',
				note: "As OpenPnP's: a known point walks radial lines through the view at two heights (up-looking: the nozzle tip; down-looking: two calibration fiducials at different heights); lens distortion (fish-eye), tilt, distance and mm/px at any Z are fitted, and every frame is corrected into a straight top view (+X right, +Y up). While on, Rotation and Flip under Image transforms don't apply.",
				actions: ['advanced-calibration'],
				fields: [
					{ path: 'calibration.enabled', label: 'Correct the image', widget: 'bool' },
					{ path: 'calibration.radial_lines', label: 'Radial lines', widget: 'integer' },
					{ path: 'calibration.fill', label: 'Out to', widget: 'number', unit: '× the view' },
					{ path: 'calibration.secondary_z_offset', label: 'Second height', widget: 'number', unit: 'mm above', when: (c) => c.value.looking !== 'down' },
					{ path: 'calibration.tangential', label: 'Tangential distortion', widget: 'bool' }
				]
			},
			{
				title: 'Calibration fiducials',
				note: 'A round mark at board height, and a second one a few mm higher (e.g. on a block), both away from other marks.',
				when: (c) => c.value.looking === 'down',
				inline: true,
				fields: [
					{ path: 'calibration.fiducial.x', label: 'Primary X', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'calibration.fiducial.y', label: 'Y', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'calibration.fiducial.z', label: 'Z', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'calibration.fiducial.diameter', label: 'Diameter', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'calibration.secondary_fiducial.x', label: 'Secondary X', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'calibration.secondary_fiducial.y', label: 'Y', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'calibration.secondary_fiducial.z', label: 'Z', widget: 'number', unit: 'mm', narrow: true },
					{ path: 'calibration.secondary_fiducial.diameter', label: 'Diameter', widget: 'number', unit: 'mm', narrow: true }
				]
			}
		]
	},
	{
		id: 'transforms',
		label: 'Image transforms',
		groups: [
			{
				title: 'Orientation',
				fields: [
					{ path: 'transform.rotate', label: 'Rotation', widget: 'number', unit: '° counter-clockwise', help: 'Any angle, as OpenPnP. If Measure reports the image rotated by r°, subtract r' },
					{ path: 'transform.flip_x', label: 'Flip vertical (flip X)', widget: 'bool', help: 'About the X axis: top-bottom, as OpenPnP’s flipX' },
					{ path: 'transform.flip_y', label: 'Flip horizontal (flip Y)', widget: 'bool', help: 'About the Y axis: left-right, as OpenPnP’s flipY' }
				]
			},
			{
				title: 'Picture',
				note: "OpenPnP's order: deinterlace, crop, white balance, (advanced calibration), scale, rotate, offset, flip.",
				fields: [
					{ path: 'transform.deinterlace', label: 'Deinterlace', widget: 'bool' },
					{ path: 'transform.white_balance.red', label: 'Red gain', widget: 'number' },
					{ path: 'transform.white_balance.green', label: 'Green gain', widget: 'number' },
					{ path: 'transform.white_balance.blue', label: 'Blue gain', widget: 'number' }
				]
			},
			{
				title: 'Scale and offset',
				inline: true,
				fields: [
					{ path: 'transform.scale.width', label: 'Scale to width', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'transform.scale.height', label: 'Height', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'transform.offset.x', label: 'Offset X', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'transform.offset.y', label: 'Offset Y', widget: 'integer', unit: 'px', narrow: true }
				]
			},
			{
				title: 'Crop',
				note: 'Pixels of the sensor image to keep. Leave empty for the whole image.',
				inline: true,
				fields: [
					{ path: 'transform.crop.x', label: 'Left', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'transform.crop.y', label: 'Top', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'transform.crop.width', label: 'Width', widget: 'integer', unit: 'px', narrow: true },
					{ path: 'transform.crop.height', label: 'Height', widget: 'integer', unit: 'px', narrow: true }
				]
			}
		]
	},
	{
		id: 'settling',
		label: 'Settling & stream',
		groups: [
			{
				title: 'Camera settling',
				note: "As OpenPnP: wait a fixed time after a move, or take pictures until the image stops moving.",
				fields: [
					{ path: 'settle_method', label: 'Method', widget: 'select', options: [['fixed', 'Fixed time'], ['motion', 'Until the image is still']] },
					{ path: 'settle', label: 'Settle time', widget: 'number', unit: 'ms' },
					{ path: 'settle_threshold', label: 'Still below', widget: 'number', unit: 'mean difference' },
					{ path: 'settle_debounce', label: 'Still pictures in a row', widget: 'integer' },
					{ path: 'settle_timeout', label: 'At most', widget: 'number', unit: 'ms' },
					{ path: 'warmup', label: 'Warm-up', widget: 'number', unit: 'ms' }
				]
			},
			{
				title: 'Light',
				note: "As OpenPnP's camera light actuator.",
				fields: [
					{ path: 'light', label: 'Light actuator', widget: 'ref', ref: 'actuators' },
					{ path: 'light_on_capture', label: 'On before a picture', widget: 'bool' },
					{ path: 'light_off_after_capture', label: 'Off after the picture', widget: 'bool' },
					{ path: 'light_on_user_action', label: 'On when the camera is moved', widget: 'bool' },
					{ path: 'light_off_for_other_cameras', label: 'Off while other cameras take pictures', widget: 'bool' }
				]
			},
			{
				title: 'Stream',
				fields: [
					{ path: 'stream_fps', label: 'Stream frame rate', widget: 'number', unit: 'fps' },
					{ path: 'jpeg_quality', label: 'JPEG quality', widget: 'integer', unit: '%' },
					{ path: 'idle_close', label: 'Close when idle after', widget: 'number', unit: 'ms' }
				]
			},
			{
				title: 'Watchdog',
				when: isType('v4l2'),
				fields: [
					{ path: 'watchdog.no_frame_timeout', label: 'No frame for', widget: 'number', unit: 'ms' },
					{ path: 'watchdog.reopen', label: 'Reopen after a failure', widget: 'bool' },
					{ path: 'watchdog.reopen_delay', label: 'Reopen after', widget: 'number', unit: 'ms' }
				]
			}
		]
	}
];

// ── nozzles and tips ─────────────────────────────────────────────────────────────────────────────────────────────
const nozzle: TabSpec[] = [
	{
		id: 'general',
		label: 'General',
		groups: [
			{ title: 'Nozzle', fields: [name] },
			{
				title: 'Axes',
				fields: [
					{ path: 'axes.z', label: 'Z axis', widget: 'ref', ref: 'axes' },
					{ path: 'axes.rotation', label: 'Rotation axis', widget: 'ref', ref: 'axes' }
				]
			},
			{ title: 'Notes', fields: [notes] }
		]
	},
	{
		id: 'position',
		label: 'Position',
		groups: [
			{
				title: 'Offset from the head',
				note: 'The reference nozzle has offset 0; the others are measured against it (e.g. both centred over the bottom camera).',
				inline: true,
				fields: xyzr('offset')
			},
			{
				title: 'Safe Z',
				fields: [
					{ path: 'dynamic_safe_z', label: 'Dynamic safe Z', widget: 'bool', help: "With a part on, its underside clears the safe Z (OpenPnP's dynamic safe Z)" }
				]
			}
		]
	},
	{
		id: 'tips',
		label: 'Nozzle tips',
		groups: [
			{
				title: 'Nozzle tips',
				actions: ['tip-change'],
				fields: [
					{ path: 'tip', label: 'Tip at start', widget: 'ref', ref: 'nozzle_tips', help: 'Loaded when nothing else was recorded; tip changes are remembered' },
					{ path: 'compatible_tips', label: 'Compatible tips', widget: 'refs', ref: 'nozzle_tips' }
				]
			}
		]
	},
	{
		id: 'vacuum',
		label: 'Vacuum',
		groups: [
			{
				title: 'Vacuum',
				fields: [
					{ path: 'vacuum', label: 'Vacuum actuator', widget: 'ref', ref: 'actuators', help: 'The valve that puts vacuum on this nozzle' },
					{ path: 'blow_off', label: 'Blow-off actuator', widget: 'ref', ref: 'actuators', help: 'On for the place dwell after the valve closes (OpenPnP)' },
					{ path: 'vacuum_sense', label: 'Vacuum sensor', widget: 'ref', ref: 'actuators', help: "A readable actuator: part-on / part-off checks with the tip's ranges" },
					{ path: 'pick_dwell', label: 'Pick dwell', widget: 'number', unit: 'ms' },
					{ path: 'place_dwell', label: 'Place dwell', widget: 'number', unit: 'ms' }
				]
			}
		]
	}
];

const nozzleTipCalibration: TabSpec = {
	id: 'calibration',
		label: 'Calibration',
		groups: [
			{
				title: 'Runout calibration',
				note: 'As OpenPnP: off by default. When on, the measured runout is compensated. It is stored per nozzle and reset when the up-looking camera or the nozzle’s offset changes. Measure from the Cameras page (Calibrate tip).',
				fields: [
					{ path: 'calibration.enabled', label: 'Enabled', widget: 'bool' },
					{ path: 'calibration.recalibrate', label: 'Measure again', widget: 'select', options: [['manual', 'By hand'], ['machine_home', 'After every homing'], ['tip_change', 'After the tip is loaded']] },
					{ path: 'calibration.allow_misdetections', label: 'Allowed misdetections', widget: 'integer' },
					{ path: 'calibration.offset_threshold', label: 'Offset threshold', widget: 'number', unit: 'mm' }
				]
			}
		]
};

const nozzleTip: TabSpec[] = [
	{
		id: 'general',
		label: 'General',
		groups: [
			{ title: 'Nozzle tip', fields: [name] },
			{
				title: 'Size',
				fields: [
					{ path: 'diameter', label: 'Tip diameter', widget: 'number', unit: 'mm' },
					{ path: 'max_part_diameter', label: 'Largest part', widget: 'number', unit: 'mm' },
					{ path: 'max_part_height', label: 'Tallest part', widget: 'number', unit: 'mm' }
				]
			},
			{
				title: 'Dwell',
				note: 'Added to the nozzle’s dwell times, as OpenPnP.',
				fields: [
					{ path: 'pick_dwell', label: 'Pick dwell', widget: 'number', unit: 'ms' },
					{ path: 'place_dwell', label: 'Place dwell', widget: 'number', unit: 'ms' }
				]
			},
			{ title: 'Notes', fields: [notes] }
		]
	},
	nozzleTipCalibration,
	{
		id: 'vacuum',
		label: 'Vacuum',
		groups: [
			{
				title: 'Vacuum sensing',
				note: "As OpenPnP's (Absolute): needs a vacuum sensor on the nozzle. A check without its range is skipped.",
				fields: [
					{ path: 'vacuum.part_on', label: 'Part on', widget: 'range', help: 'Level with a part on the nozzle' },
					{ path: 'vacuum.part_off', label: 'Part off', widget: 'range', help: 'Level with nothing on it' },
					{ path: 'vacuum.check_after_pick', label: 'Check after the pick', widget: 'bool' },
					{ path: 'vacuum.check_after_place', label: 'Check after the place', widget: 'bool' }
				]
			}
		]
	},
	{
		id: 'changer',
		label: 'Tool changer',
		groups: [
			{
				title: 'Holder path',
				note: "As OpenPnP's: load goes start → middle 1 → middle 2 → end, unload the other way. Without these the tip is changed by hand.",
				inline: true,
				fields: [
					...xyzr('changer.start', false).map((f) => ({ ...f, label: `Start ${f.label}` })),
					...xyzr('changer.middle1', false).map((f) => ({ ...f, label: `Middle 1 ${f.label}` })),
					...xyzr('changer.middle2', false).map((f) => ({ ...f, label: `Middle 2 ${f.label}` })),
					...xyzr('changer.end', false).map((f) => ({ ...f, label: `End ${f.label}` }))
				]
			},
			{
				title: 'Speeds',
				fields: [
					{ path: 'changer.start_to_middle1_speed', label: 'Start ↔ middle 1', widget: 'number', unit: '× feed' },
					{ path: 'changer.middle1_to_middle2_speed', label: 'Middle 1 ↔ middle 2', widget: 'number', unit: '× feed' },
					{ path: 'changer.middle2_to_end_speed', label: 'Middle 2 ↔ end', widget: 'number', unit: '× feed' }
				]
			}
		]
	}
];

// ── axes ─────────────────────────────────────────────────────────────────────────────────────────────────────────
const axis: TabSpec[] = [
	{
		id: 'general',
		label: 'General',
		groups: [
			{
				title: 'Axis',
				fields: [
					name,
					{ path: 'kind', label: 'Kind', widget: 'select', options: [['linear', 'Linear (mm)'], ['rotation', 'Rotation (degrees)']], when: isType('controller', 'virtual') }
				]
			},
			{
				title: 'Controller',
				when: isType('controller'),
				fields: [
					{ path: 'driver', label: 'Driver', widget: 'ref', ref: 'drivers' },
					{ path: 'letter', label: 'Axis letter', widget: 'select', options: ['X', 'Y', 'Z', 'A', 'B', 'C', 'U', 'V', 'W'] }
				]
			},
			{
				title: 'Cam',
				when: isType('cam'),
				note: "As OpenPnP's cam axes: z = radius · sin(angle + 90° − arms/2) + wheel radius + gap; the clockwise nozzle mirrored.",
				fields: [
					{ path: 'input', label: 'Rotary axis', widget: 'ref', ref: 'axes' },
					{ path: 'direction', label: 'Direction', widget: 'select', options: [['counter_clockwise', 'Counter-clockwise'], ['clockwise', 'Clockwise']] },
					{ path: 'cam_radius', label: 'Cam radius', widget: 'number', unit: 'mm' },
					{ path: 'wheel_radius', label: 'Wheel radius', widget: 'number', unit: 'mm' },
					{ path: 'wheel_gap', label: 'Wheel gap', widget: 'number', unit: 'mm' },
					{ path: 'arms_angle', label: 'Arms angle', widget: 'number', unit: '°' }
				]
			},
			{
				title: 'Mapping',
				when: isType('mapped'),
				note: 'This axis is a linear function of another, given by two points [[input, output], [input, output]].',
				fields: [
					{ path: 'input', label: 'Input axis', widget: 'ref', ref: 'axes' },
					{ path: 'map', label: 'Map', widget: 'json' }
				]
			},
			{ title: 'Notes', fields: [notes] }
		]
	},
	{
		id: 'homing',
		label: 'Homing & limits',
		when: isType('controller', 'virtual'),
		groups: [
			{
				title: 'Homing',
				fields: [
					{ path: 'homing', label: 'Has a home switch', widget: 'bool', when: isType('controller') },
					{ path: 'home', label: 'Position after homing', widget: 'number' }
				]
			},
			{
				title: 'Limits',
				when: isType('controller'),
				fields: [
					{ path: 'soft_limits', label: 'Soft limits', widget: 'range', help: 'Moves outside are refused' },
					{ path: 'safe_zone', label: 'Safe zone', widget: 'range', help: 'Z: the range where the nozzles are up (safe Z)' },
					{ path: 'limit_rotation', label: 'Rotation range', widget: 'range', help: 'Rotation axes: angles are taken by whole turns into this range (OpenPnP limit rotation)' },
					{ path: 'wrap_around', label: 'Turn the shortest way', widget: 'bool', help: 'Rotation axes without a range (OpenPnP wrap around)' }
				]
			}
		]
	},
	{
		id: 'kinematics',
		label: 'Kinematics',
		when: isType('controller'),
		groups: [
			{
				title: 'Speed',
				note: 'Values may carry their unit, e.g. 8000 mm/min.',
				fields: [
					{ path: 'feed', label: 'Feed rate', widget: 'number', unit: 'mm/s' },
					{ path: 'acceleration', label: 'Acceleration', widget: 'number', unit: 'mm/s²' },
					{ path: 'acceleration_applies_in', label: 'Acceleration is set in', widget: 'select', options: [['firmware', 'Firmware (informational)'], ['driver', 'Driver (sent with moves)']] }
				]
			},
			{
				title: 'Backlash compensation',
				note: "As OpenPnP's: one-sided positioning always approaches the target from the side of the offset (optimized: only when it would come from the other side).",
				fields: [
					{ path: 'backlash.method', label: 'Method', widget: 'select', options: [['none', 'None'], ['one_sided', 'One-sided'], ['one_sided_optimized', 'One-sided, optimized']] },
					{ path: 'backlash.offset', label: 'Offset', widget: 'number', help: 'Signed: the side it comes from, e.g. -0.1' },
					{ path: 'backlash.speed_factor', label: 'Final approach speed', widget: 'number', unit: '× feed' }
				]
			},
			{
				title: 'Resolution',
				fields: [
					{ path: 'resolution', label: 'Resolution (one step)', widget: 'number', help: 'Smaller jogs offer this step instead' },
					{ path: 'tolerance', label: 'Tolerance', widget: 'number', help: 'Positions this close count as reached. Default: half the resolution (0.0001 without one, as OpenPnP)' }
				]
			}
		]
	}
];

// ── heads ────────────────────────────────────────────────────────────────────────────────────────────────────────
const head: TabSpec[] = [
	{
		id: 'general',
		label: 'General',
		groups: [
			{ title: 'Head', fields: [name] },
			{
				title: 'Axes',
				fields: [
					{ path: 'axes.x', label: 'X axis', widget: 'ref', ref: 'axes' },
					{ path: 'axes.y', label: 'Y axis', widget: 'ref', ref: 'axes' }
				]
			},
			{
				title: 'Vacuum pump',
				fields: [
					{ path: 'pump', label: 'Pump actuator', widget: 'ref', ref: 'actuators' },
					{ path: 'pump_control', label: 'Pump control', widget: 'select', options: [['part_on', 'On while a part is carried'], ['task_duration', 'On until the job ends'], ['keep_running', 'Keep running until disabled'], ['none', 'Never switched']], help: "As OpenPnP's vacuum pump control; off on disable in every mode" },
					{ path: 'pump_settle', label: 'Wait after switching on', widget: 'number', unit: 'ms' }
				]
			},
			{
				title: 'Actuators on this head',
				fields: [{ path: 'actuators', label: 'Actuators', widget: 'refs', ref: 'actuators' }]
			},
			{ title: 'Notes', fields: [notes] }
		]
	}
];

// ── actuators ────────────────────────────────────────────────────────────────────────────────────────────────────
const actuator: TabSpec[] = [
	{
		id: 'general',
		label: 'General',
		groups: [
			{
				title: 'Actuator',
				fields: [
					name,
					{ path: 'value', label: 'Value type', widget: 'select', options: [['boolean', 'On / off'], ['double', 'Number'], ['string', 'Text']] },
					{ path: 'driver', label: 'Driver', widget: 'ref', ref: 'drivers', when: isType('gcode') },
					{ path: 'index', label: 'Index', widget: 'integer', when: isType('gcode'), help: "{Index} in the driver's ACTUATE_* commands" },
					{ path: 'on_value', label: 'On value', widget: 'number', when: (c) => c.value.value === 'double', help: 'Number actuators switched on/off (e.g. a PWM vacuum valve), as OpenPnP' },
					{ path: 'off_value', label: 'Off value', widget: 'number', when: (c) => c.value.value === 'double' }
				],
				actions: ['actuator-test']
			},
			{ title: 'Notes', fields: [notes] }
		]
	},
	{
		id: 'gcode',
		label: 'G-code',
		when: isType('gcode'),
		groups: [
			{
				title: 'Switching',
				fields: [
					{ path: 'on', label: 'On', placeholder: 'M64 P0' },
					{ path: 'off', label: 'Off', placeholder: 'M65 P0' },
					{ path: 'set', label: 'Set a value', placeholder: 'M3 S{IntegerValue}', help: 'For number or text actuators' }
				]
			},
			{
				title: 'Reading',
				fields: [
					{ path: 'read', label: 'Read command' },
					{ path: 'read_regex', label: 'Reading regex', help: 'With a group named Value' }
				]
			}
		]
	},
	{
		id: 'events',
		label: 'Machine events',
		groups: [
			{
				title: 'Set automatically',
				note: 'A value set when the machine is enabled or disabled, e.g. off.',
				fields: [
					{ path: 'on_enable', label: 'On enable', widget: 'json' },
					{ path: 'on_disable', label: 'On disable', widget: 'json' }
				]
			}
		]
	}
];

// ── drivers ──────────────────────────────────────────────────────────────────────────────────────────────────────
const driver: TabSpec[] = [
	{
		id: 'general',
		label: 'General',
		groups: [
			{ title: 'Driver', fields: [name] },
			{
				title: 'Connection',
				when: isType('gcode'),
				fields: [
					{ path: 'port', label: 'Port', placeholder: '/dev/serial0', help: 'Serial device, tcp://host:port, or sim://grblhal' },
					{ path: 'baud', label: 'Baud rate', widget: 'integer' },
					{ path: 'connect_wait', label: 'Wait after opening', widget: 'number', unit: 'ms' },
					{ path: 'disconnect_on_disable', label: 'Close the port on disable', widget: 'bool' }
				]
			},
			{
				title: 'Simulation',
				when: isType('simulated'),
				fields: [
					{ path: 'time_scale', label: 'Time scale', widget: 'number' },
					{ path: 'home_time', label: 'Homing takes', widget: 'number', unit: 'ms' }
				]
			},
			{ title: 'Notes', fields: [notes] }
		]
	},
	{
		id: 'gcode',
		label: 'G-code',
		when: isType('gcode'),
		groups: [
			{
				title: 'Profile',
				fields: [
					{ path: 'profile', label: 'Profile', widget: 'select', options: [['generic', 'Generic'], ['grblhal', 'grblHAL']], help: 'Built-in commands; the ones below override it' }
				]
			},
			{
				title: 'Commands',
				note: "OpenPnP's command names and template syntax.",
				fields: [{ path: 'commands', label: 'Commands', widget: 'commands' }]
			},
			{
				title: 'Text',
				fields: [
					{ path: 'letter_variables', label: 'Variables by axis letter', widget: 'bool', help: '{X} {Y} {Z} {A} {B} instead of {X} {Y} {Z} {Rotation}' },
					{ path: 'backslash_escapes', label: 'Decode \\ escapes', widget: 'bool', help: 'For commands copied from OpenPnP' },
					{ path: 'line_ending', label: 'Line ending', widget: 'select', options: [['\n', 'LF (\\n)'], ['\r\n', 'CR LF (\\r\\n)']] },
					{ path: 'remove_comments', label: 'Remove comments', widget: 'bool' },
					{ path: 'compress_gcode', label: 'Compress G-code', widget: 'bool' }
				]
			}
		]
	},
	{
		id: 'motion',
		label: 'Motion & timing',
		when: isType('gcode'),
		groups: [
			{
				title: 'Motion',
				fields: [
					{ path: 'max_feed', label: 'Feed rate cap', widget: 'number', unit: 'mm/s' },
					{ path: 'motion_control', label: 'Feed rate per move', widget: 'select' },
					{ path: 'location_confirmation', label: 'Read the position after every move', widget: 'bool' },
					{ path: 'position_poll', label: 'Poll the position while moving', widget: 'number', unit: 'ms' }
				]
			},
			{
				title: 'Timeouts',
				fields: [
					{ path: 'timeout', label: 'Command', widget: 'number', unit: 'ms' },
					{ path: 'home_timeout', label: 'Homing', widget: 'number', unit: 'ms' },
					{ path: 'move_timeout', label: 'Move', widget: 'number', unit: 'ms' },
					{ path: 'settle', label: 'Settle after commands', widget: 'json' }
				]
			},
			{
				title: 'Stopping',
				fields: [
					{ path: 'realtime', label: 'Real-time commands', widget: 'list', help: 'Sent as bytes without waiting, e.g. ?' },
					{ path: 'stop', label: 'Stop sequence', widget: 'list' },
					{ path: 'cancel', label: 'Jog cancel', widget: 'list' },
					{ path: 'after_stop', label: 'State after stop', widget: 'select' }
				]
			}
		]
	}
];

// ── the machine section ──────────────────────────────────────────────────────────────────────────────────────────
const machine: TabSpec[] = [
	{
		id: 'events',
		label: 'Enable & homing',
		groups: [
			{
				title: 'Steps',
				note: 'Steps such as home, safe_z, park, or {actuate: {valve1: off}}.',
				fields: [
					{ path: 'on_enable', label: 'On enable', widget: 'json' },
					{ path: 'on_before_home', label: 'Before homing', widget: 'json', help: 'Run only when the machine is homed (position known)' },
					{ path: 'on_home', label: 'After homing', widget: 'json' }
				]
			},
			{
				title: 'Park',
				fields: [
					{ path: 'park.head', label: 'Head', widget: 'ref', ref: 'heads' },
					...xyzr('park.location', false)
				]
			}
		]
	},
	{
		id: 'jog',
		label: 'Jogging',
		groups: [
			{
				title: 'Jogging',
				fields: [
					{ path: 'jog.distances', label: 'Jog distances', widget: 'numbers', unit: 'mm' },
					{ path: 'jog.safe_z', label: 'Before X/Y jogs', widget: 'select', options: [['never', 'Stay at the current Z (as OpenPnP)'], ['always', 'Raise the nozzles to safe Z']] },
					{ path: 'unsafe_z_roaming_distance', label: 'Unsafe Z roaming', widget: 'number', unit: 'mm', help: 'As OpenPnP: virtual-Z tools jogged farther than this below safe Z go back up' },
					{ path: 'auto_tool_select', label: 'Auto tool select', widget: 'bool', help: 'The tool you move becomes the selected one (OpenPnP)' }
				]
			}
		]
	},
	{
		id: 'other',
		label: 'Locations & macros',
		groups: [
			{ title: 'Discard location', inline: true, fields: xyzr('discard_location', false) },
			{ title: 'Macros', note: 'Named step lists, run from the Machine page.', fields: [{ path: 'macros', label: 'Macros', widget: 'json' }] }
		]
	}
];

const LAYOUTS: Record<string, TabSpec[]> = {
	camera,
	nozzle,
	nozzle_tip: nozzleTip,
	axis,
	head,
	actuator,
	driver,
	machine
};

/** The hand-made tabs for an object, without those that don't apply to it. Empty: no layout (a plugin type ...). */
export function layoutFor(ctx: LayoutContext): TabSpec[] {
	const tabs = LAYOUTS[ctx.kind] ?? [];
	return tabs
		.filter((t) => !t.when || t.when(ctx))
		.map((t) => ({ ...t, groups: t.groups.filter((g) => !g.when || g.when(ctx)).map((g) => ({ ...g, fields: g.fields.filter((f) => !f.when || f.when(ctx)) })) }))
		.filter((t) => t.groups.length);
}

// ── values at dotted paths ──────────────────────────────────────────────────────────────────────────────────────
type Obj = Record<string, unknown>;

export function getAt(obj: Obj | undefined, path: string): unknown {
	return path.split('.').reduce<unknown>((node, key) => (node && typeof node === 'object' ? (node as Obj)[key] : undefined), obj);
}

/** A copy of `obj` with the value at `path` replaced (undefined removes it; objects left empty are removed too). */
export function setAt(obj: Obj, path: string, value: unknown): Obj {
	const [head, ...rest] = path.split('.');
	const next: Obj = { ...obj };
	const v = rest.length ? setAt((obj[head] as Obj | undefined) ?? {}, rest.join('.'), value) : value;
	if (v === undefined || (rest.length && Object.keys(v as Obj).length === 0)) delete next[head];
	else next[head] = v;
	return next;
}
