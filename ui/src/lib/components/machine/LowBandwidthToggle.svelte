<script lang="ts">
	// Switch camera pictures in this browser between full and low bandwidth (see stream.svelte.ts).
	import { Button } from '#lib/components/ui/button/index.js';
	import { streamSettings, LOW_BANDWIDTH } from '#lib/stream.svelte.js';
	import { Gauge } from '@lucide/svelte';

	const p = LOW_BANDWIDTH;
</script>

<Button
	variant={streamSettings.lowBandwidth ? 'default' : 'outline'}
	size="sm"
	aria-pressed={streamSettings.lowBandwidth}
	onclick={() => streamSettings.toggle()}
	title="Low bandwidth: {p.fps} fps, half size, JPEG quality {p.quality}, for a slow network (the picture lags otherwise). Only this browser's picture changes; vision uses full frames"
>
	<Gauge /> Low bandwidth
</Button>
