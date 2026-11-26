// The picture's orientation as measured (config.capture units_per_pixel): words for it, and the fix.

export type Orientation = {
	mirrored: boolean;
	rotation_deg: number;
	needs_correction: boolean;
	correction: { rotate: number; flip_x: boolean; flip_y: boolean };
};

/** "upside down", "turned 90° clockwise", "a mirror image, turned 3.2°", ... */
export function describeOrientation(o: Orientation): string {
	const r = ((o.rotation_deg % 360) + 540) % 360 - 180; // -180..180
	const a = Math.abs(r);
	let turn = '';
	if (Math.abs(a - 180) < 1) turn = 'upside down';
	else if (Math.abs(a - 90) < 1) turn = `turned 90° ${r > 0 ? 'counter-clockwise' : 'clockwise'}`;
	else if (a > 0.05) turn = `turned ${a.toFixed(2)}° ${r > 0 ? 'counter-clockwise' : 'clockwise'}`;
	if (o.mirrored) return turn ? `a mirror image, ${turn}` : 'a mirror image';
	return turn || 'upright';
}

/** The transform settings that put it right (+X right, +Y up). */
export function correctionText(o: Orientation): string {
	const c = o.correction;
	const parts = [`rotation ${c.rotate}°`];
	if (c.flip_y) parts.push('flip horizontal');
	return parts.join(', ');
}
