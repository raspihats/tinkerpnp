<script lang="ts">
	// Fill a V4L2 camera's device selector from the cameras the server sees (camera.devices). Two cameras of one model
	// often share a serial number (the bench's Sonix: SN0001), so a serial is used only when it's unique; the USB port
	// path (/dev/v4l/by-path/...) is always added, as it stays the same while the camera stays in its port.
	import { tinker } from '#lib/api/client.svelte.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { RefreshCw } from '@lucide/svelte';

	type Device = {
		path: string;
		name: string | null;
		usb_id: string | null;
		usb_serial: string | null;
		product: string | null;
		links: string[];
		capture: boolean;
		/** configured cameras that pick this device */
		used_by?: string[];
	};

	let { onpick, self = null }: { onpick: (device: Record<string, string>) => void; self?: string | null } = $props();

	const others = (d: Device) => (d.used_by ?? []).filter((u) => u !== self);
	const mine = (d: Device) => !!self && (d.used_by ?? []).includes(self);
	function usage(d: Device): string {
		const o = others(d);
		if (mine(d)) return o.length ? `this camera, also ${o.join(', ')}` : 'this camera';
		return o.length ? `used by ${o.join(', ')}` : 'free';
	}

	let devices = $state<Device[]>([]);
	let error = $state<string | null>(null);
	let chosen = $state('');

	async function load() {
		try {
			devices = (await tinker.call<Device[]>('camera.devices')).filter((d) => d.capture && d.usb_id);
			error = null;
		} catch (e) {
			error = String(e);
		}
	}
	$effect(() => {
		if (tinker.connection === 'connected') load();
	});

	const byPath = (d: Device) => d.links.find((l) => l.includes('/by-path/')) ?? null;
	const port = (d: Device) => byPath(d)?.match(/usb-[\d.:-]+:([\d.]+):/)?.[1] ?? null;

	function pick(path: string) {
		const d = devices.find((x) => x.path === path);
		if (!d) return;
		const sameSerial = devices.filter((x) => x.usb_serial && x.usb_serial === d.usb_serial).length;
		const sel: Record<string, string> = {};
		if (d.usb_serial && sameSerial === 1) sel.usb_serial = d.usb_serial;
		if (d.usb_id && sameSerial <= 1 && devices.filter((x) => x.usb_id === d.usb_id).length === 1) sel.usb_id = d.usb_id;
		sel.path = byPath(d) ?? d.path;
		onpick(sel);
	}
</script>

<div class="flex flex-wrap items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2 text-xs">
	<span class="font-medium">Detected cameras</span>
	{#if devices.length}
		<select
			class="h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-xs"
			bind:value={chosen}
			onchange={() => pick(chosen)}
		>
			<option value="" disabled>Pick one to fill in the device…</option>
			{#each devices as d (d.path)}
				<option value={d.path} class={others(d).length ? 'bg-warn/20 text-warn' : mine(d) ? 'bg-primary/15 text-primary' : ''}>
					{others(d).length ? '● ' : mine(d) ? '◆ ' : '○ '}[{usage(d)}] {d.product ?? d.name} · {d.usb_id}{d.usb_serial ? ` · serial ${d.usb_serial}` : ''}{port(d) ? ` · USB port ${port(d)}` : ''} · {d.path}
				</option>
			{/each}
		</select>
	{:else}
		<span class="text-muted-foreground">{error ?? 'No USB camera found on the server.'}</span>
	{/if}
	<Button variant="ghost" size="icon-sm" title="Look again" onclick={load}><RefreshCw /></Button>
</div>
