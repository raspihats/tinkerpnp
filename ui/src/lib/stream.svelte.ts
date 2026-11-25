// How camera pictures are sent to this browser. "Low bandwidth" asks the server for fewer, smaller and more compressed
// frames, for a slow link (e.g. the Pi on weak WiFi, where a full stream backs up and the picture lags by seconds).
// Only the picture shown here changes: vision always works on full frames. Remembered per browser.

const KEY = 'tinkerpnp.stream';
export const LOW_BANDWIDTH = { fps: 5, quality: 60, scale: 0.5 };

class StreamSettings {
	lowBandwidth = $state(false);

	constructor() {
		try {
			this.lowBandwidth = JSON.parse(localStorage.getItem(KEY) ?? '{}').lowBandwidth === true;
		} catch {
			/* no storage: default */
		}
	}

	toggle(): void {
		this.lowBandwidth = !this.lowBandwidth;
		try {
			localStorage.setItem(KEY, JSON.stringify({ lowBandwidth: this.lowBandwidth }));
		} catch {
			/* not remembered */
		}
	}

	/** Query parameters for stream.mjpg / snapshot.jpg (without overlay). */
	params(): Record<string, string> {
		if (!this.lowBandwidth) return {};
		const p = LOW_BANDWIDTH;
		return { fps: String(p.fps), quality: String(p.quality), scale: String(p.scale) };
	}
}

export const streamSettings = new StreamSettings();
