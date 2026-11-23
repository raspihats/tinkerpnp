<script lang="ts">
	import { tick } from 'svelte';
	import { tinker, RpcError, idOf } from '#lib/api/client.svelte.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { TriangleAlert } from '@lucide/svelte';

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
		if (l.direction === 'info') return 'text-destructive';
		if (l.direction === 'tx') return 'text-sky-700 dark:text-sky-400';
		if (/^(error|ALARM)/.test(l.line)) return 'text-destructive font-semibold';
		if (/^ok/.test(l.line)) return 'text-muted-foreground';
		return '';
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

<div class="flex h-[calc(100vh-9rem)] flex-col gap-3">
	<div class="flex flex-wrap items-center gap-2 text-sm">
		{#if drivers.length > 1}
			{#each drivers as d (d)}
				<button onclick={() => (driver = d)}><Badge variant={driver === d ? 'default' : 'outline'}>{d}</Badge></button>
			{/each}
		{:else if driver}
			<span class="font-mono">driver/{driver}</span>
		{/if}
		{#if driverStatus}
			<Badge variant={driverStatus.connected ? 'secondary' : 'outline'}>{driverStatus.connected ? 'connected' : 'not connected'}</Badge>
			{#if driverStatus.firmware}<span class="text-xs text-muted-foreground">{driverStatus.firmware}</span>{/if}
			{#if driverStatus.last_error}<span class="text-xs text-destructive">{driverStatus.last_error}</span>{/if}
		{/if}
		<label class="ml-auto flex items-center gap-1.5 text-xs">
			<input type="checkbox" bind:checked={hideStatus} /> hide status reports
		</label>
		<Button variant="ghost" size="sm" onclick={() => (lines = [])}>Clear</Button>
	</div>

	{#if !drivers.length}
		<Card.Card class="py-4"><Card.CardContent class="text-sm text-muted-foreground">
			This machine has no G-code driver.
		</Card.CardContent></Card.Card>
	{:else}
		<div
			bind:this={logEl}
			onscroll={onScroll}
			class="min-h-0 flex-1 overflow-y-auto rounded-lg border bg-background p-2 font-mono text-xs leading-5"
		>
			{#each shown as l (l.n)}
				<div class="whitespace-pre-wrap break-all {lineClass(l)}">
					<span class="mr-2 select-none text-muted-foreground">{l.direction === 'tx' ? '→' : l.direction === 'rx' ? '←' : '!'}</span>{shownText(l.line)}
				</div>
			{/each}
		</div>

		{#if isGrbl}
			<div class="flex flex-wrap gap-1">
				{#each grblQuick as q (q.label)}
					<Button variant="outline" size="sm" class="font-mono" title={q.title} disabled={sending && q.cmd.length > 1} onclick={() => send(q.cmd)}>{q.label}</Button>
				{/each}
			</div>
		{/if}

		<div class="flex items-center gap-2">
			<Input
				class="font-mono"
				placeholder={'G-code, e.g. G1 X10 F3000 — several lines with \\n, control bytes as \\x18'}
				bind:value={command}
				onkeydown={onKey}
				spellcheck={false}
				autocomplete="off"
			/>
			<Button disabled={!command.trim() || sending} onclick={() => send()}>Send</Button>
		</div>
		<p class="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
			<TriangleAlert class="size-3.5" />
			Commands go straight to the controller: no soft limits, no safe Z. The position is read back afterwards.
		</p>
	{/if}
</div>
