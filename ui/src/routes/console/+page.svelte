<script lang="ts">
	import { tick } from 'svelte';
	import { tinker, RpcError, idOf } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { segmentItem } from '#lib/styles.js';
	import { TriangleAlert, SquareTerminal, Eraser, SendHorizontal, ChevronRight } from '@lucide/svelte';

	type Line = { n: number; driver: string; direction: 'tx' | 'rx' | 'info'; line: string };

	const MAX_LINES = 1000;
	const HISTORY_KEY = 'tinkerui.console.history';

	let lines = $state<Line[]>([]);
	let counter = 0;
	let command = $state('');
	let sending = $state(false);
	let hideStatus = $state(true);
	let follow = $state(true);
	let logEl = $state<HTMLDivElement | null>(null);
	let driver = $state<string | null>(null);

	// command history (up/down arrows), kept in this browser only
	let history: string[] = loadHistory();
	let historyPos = -1;

	function loadHistory(): string[] {
		try {
			return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]');
		} catch {
			return [];
		}
	}

	function saveHistory() {
		try {
			localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(-100)));
		} catch {
			/* private window: history just isn't kept */
		}
	}

	// G-code drivers of this machine
	const drivers = $derived(
		tinker.objects.filter((n) => n.startsWith('driver/') && tinker.status[n]?.type === 'gcode').map(idOf)
	);
	$effect(() => {
		if (driver === null || !drivers.includes(driver)) driver = drivers[0] ?? null;
	});
	const driverStatus = $derived(driver ? tinker.status[`driver/${driver}`] : undefined);
	const isGrbl = $derived(driverStatus?.profile === 'grblhal');

	function isStatusReport(l: Line): boolean {
		return (l.direction === 'tx' && l.line === '?') || (l.direction === 'rx' && /^<.*>$/.test(l.line));
	}

	const shown = $derived(lines.filter((l) => !(hideStatus && isStatusReport(l)) && (!driver || l.driver === driver || l.direction === 'info')));

	function push(entry: Omit<Line, 'n'>) {
		lines.push({ ...entry, n: counter++ });
		if (lines.length > MAX_LINES) lines.splice(0, lines.length - MAX_LINES);
	}

	async function scrollDown() {
		if (!follow) return;
		await tick();
		logEl?.scrollTo({ top: logEl.scrollHeight });
	}

	function onScroll() {
		if (!logEl) return;
		follow = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
	}

	// subscribe on every (re)connect; history first so the view starts with recent traffic
	$effect(() => {
		if (tinker.connection !== 'connected') return;
		let stop = () => {};
		(async () => {
			try {
				const hist = await tinker.call<Omit<Line, 'n'>[]>('gcode.history', { limit: 300 });
				lines = [];
				for (const h of hist) push(h);
				stop = tinker.on('notify_gcode', (p) => {
					push(p as unknown as Omit<Line, 'n'>);
					scrollDown();
				});
				await tinker.call('gcode.subscribe', {});
				scrollDown();
			} catch (e) {
				push({ driver: '', direction: 'info', line: `console: ${e}` });
			}
		})();
		return () => {
			stop();
			tinker.call('gcode.unsubscribe').catch(() => {});
		};
	});

	async function send(text = command) {
		if (!text.trim() || !driver) return;
		if (text === command) {
			if (history[history.length - 1] !== text) history.push(text);
			saveHistory();
			historyPos = -1;
			command = '';
		}
		sending = true;
		try {
			const r = await tinker.call<{ responses: string[]; ok: boolean }>('gcode.send', { driver, command: text }, 600000);
			if (!r.ok) push({ driver, direction: 'info', line: `✗ ${text}: ${r.responses[r.responses.length - 1]}` });
		} catch (e) {
			push({ driver, direction: 'info', line: `✗ ${text}: ${e instanceof RpcError ? e.message : e}` });
		} finally {
			sending = false;
			scrollDown();
		}
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			send();
		} else if (e.key === 'ArrowUp' && history.length) {
			e.preventDefault();
			historyPos = historyPos < 0 ? history.length - 1 : Math.max(0, historyPos - 1);
			command = history[historyPos];
		} else if (e.key === 'ArrowDown' && historyPos >= 0) {
			e.preventDefault();
			historyPos = historyPos + 1 >= history.length ? -1 : historyPos + 1;
			command = historyPos < 0 ? '' : history[historyPos];
		}
	}

	function lineClass(l: Line): string {
		if (l.direction === 'info') return 'text-alarm';
		if (l.direction === 'tx') return 'text-primary';
		if (/^(error|ALARM)/.test(l.line)) return 'text-alarm font-semibold';
		if (/^ok/.test(l.line)) return 'text-muted-foreground/70';
		if (/^\[MSG/.test(l.line)) return 'text-warn';
		return 'text-foreground/90';
	}

	function shownText(s: string): string {
		// control characters (Ctrl-X = \x18, jog cancel = \x85) as escapes
		return s.replace(/[\x00-\x1f\x7f-\x9f]/g, (c) => `\\x${c.charCodeAt(0).toString(16).padStart(2, '0')}`);
	}

	// grblHAL shortcuts: commands that read, plus the real-time ones (Ctrl-X etc. can't be typed)
	const grblQuick = [
		{ label: '$I', cmd: '$I', title: 'Build info' },
		{ label: '$$', cmd: '$$', title: 'All settings' },
		{ label: '$#', cmd: '$#', title: 'Coordinate offsets' },
		{ label: '$G', cmd: '$G', title: 'Parser state (modal G-codes)' },
		{ label: '?', cmd: '?', title: 'Status report' },
		{ label: '$X', cmd: '$X', title: 'Unlock (clear alarm)' },
		{ label: 'Ctrl-X', cmd: '\\x18', title: 'Soft reset' },
		{ label: '!', cmd: '!', title: 'Feed hold' },
		{ label: '~', cmd: '~', title: 'Resume' }
	];
</script>

<div class="flex h-[calc(100vh-7.5rem)] flex-col">
	{#if !drivers.length}
		<Card.Root><Card.Content class="text-sm text-muted-foreground">This machine has no G-code driver.</Card.Content></Card.Root>
	{:else}
		<Card.Root class="min-h-0 flex-1 gap-0 py-0">
			<Card.Header class="flex flex-wrap items-center gap-2 border-b py-2.5">
				<SquareTerminal class="size-4 text-muted-foreground" />
				{#if drivers.length > 1}
					<ToggleGroup.Root type="single" variant="outline" size="sm" bind:value={driver as string}>
						{#each drivers as d (d)}<ToggleGroup.Item value={d} class={segmentItem}>{d}</ToggleGroup.Item>{/each}
					</ToggleGroup.Root>
				{:else}
					<Card.Title class="font-mono text-sm">driver/{driver}</Card.Title>
				{/if}
				{#if driverStatus}
					<Badge variant={driverStatus.connected ? 'ok' : 'outline'}>{driverStatus.connected ? 'connected' : 'not connected'}</Badge>
					{#if driverStatus.firmware}<span class="num hidden text-xs text-muted-foreground md:inline">{driverStatus.firmware}</span>{/if}
				{/if}
				<Card.Action class="flex items-center gap-3">
					<div class="flex items-center gap-2">
						<Switch id="status" checked={!hideStatus} onCheckedChange={(v) => (hideStatus = !v)} />
						<Label for="status" class="text-xs font-normal text-muted-foreground">Status reports</Label>
					</div>
					<Button variant="ghost" size="sm" onclick={() => (lines = [])}><Eraser /> Clear</Button>
				</Card.Action>
			</Card.Header>

			<div bind:this={logEl} onscroll={onScroll} class="min-h-0 flex-1 overflow-y-auto bg-well px-4 py-3 font-mono text-[12.5px] leading-6">
				{#each shown as l (l.n)}
					<div class="break-all whitespace-pre-wrap {lineClass(l)}">
						<span class="mr-2 inline-block w-3 text-muted-foreground/60 select-none">{l.direction === 'tx' ? '›' : l.direction === 'rx' ? '' : '!'}</span>{shownText(l.line)}
					</div>
				{:else}
					<p class="text-muted-foreground">No traffic yet.</p>
				{/each}
			</div>

			<div class="flex flex-col gap-2 border-t p-3">
				{#if driverStatus?.last_error}
					<p class="flex items-center gap-1.5 text-xs text-alarm"><TriangleAlert class="size-3.5" /> {driverStatus.last_error}</p>
				{/if}
				{#if isGrbl}
					<div class="flex flex-wrap gap-1">
						{#each grblQuick as q (q.label)}
							<Button variant="outline" size="xs" class="font-mono" title={q.title} disabled={sending && q.cmd.length > 1} onclick={() => send(q.cmd)}>{q.label}</Button>
						{/each}
					</div>
				{/if}
				<div class="flex items-center gap-2">
					<div class="relative flex-1">
						<ChevronRight class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-primary" />
						<Input
							class="h-9 pl-8 font-mono"
							placeholder={'G-code, e.g. G1 X10 F3000 (several lines with \\n, control bytes as \\x18)'}
							bind:value={command}
							onkeydown={onKey}
							spellcheck={false}
							autocomplete="off"
						/>
					</div>
					<Button class="h-9" disabled={!command.trim() || sending} onclick={() => send()}><SendHorizontal /> Send</Button>
				</div>
				<p class="flex items-center gap-1.5 text-xs text-muted-foreground">
					<TriangleAlert class="size-3.5 text-warn" />
					Commands go straight to the controller: no soft limits, no safe Z. The position is read back afterwards.
					<span class="ml-auto hidden sm:inline"><kbd class="kbd">↑</kbd><kbd class="kbd">↓</kbd> history</span>
				</p>
			</div>
		</Card.Root>
	{/if}
</div>
