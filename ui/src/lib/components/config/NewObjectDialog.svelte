<script lang="ts">
	import * as Alert from '#lib/components/ui/alert/index.js';
	// "New camera…" etc., as OpenPnP's class selection dialog: pick the type, and the object is created right away with
	// defaults, then set up on its tabs. Only what the configuration can't be valid without is asked here (which
	// driver an axis is on, which axes a nozzle moves with, ...).
	import type { FieldSpec } from '#lib/config/layouts.js';
	import { getAt, setAt } from '#lib/config/layouts.js';
	import { schemaAt, type Defs, type Schema } from '#lib/config/schema.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import LayoutField from './LayoutField.svelte';
	import { Plus, X, CircleAlert } from '@lucide/svelte';

	type Obj = Record<string, unknown>;
	type TypeInfo = { kind: string; type: string; title: string; description: string; plugin: string | null; schema: Schema & { $defs?: Defs } };
	type Problem = { path: string; message: string };

	let {
		kind,
		base: initialBase,
		types,
		config,
		refs,
		label,
		oncancel,
		oncreate
	}: {
		kind: string;
		base: string;
		types: TypeInfo[];
		config: Obj;
		refs: Record<string, string[]>;
		label: string;
		oncancel: () => void;
		/** returns the problems when the configuration wouldn't be valid */
		oncreate: (path: string, value: Obj) => Promise<{ message: string; problems: Problem[] } | null>;
	} = $props();

	const heads = $derived(Object.keys((config.heads as Obj | undefined) ?? {}));
	// svelte-ignore state_referenced_locally
	let base = $state(initialBase);
	// svelte-ignore state_referenced_locally
	let typeName = $state(types.find((t) => t.type === 'v4l2')?.type ?? types.find((t) => t.type !== 'simulated')?.type ?? types[0]?.type ?? '');
	const type = $derived(types.find((t) => t.type === typeName));
	const onHead = $derived(base.startsWith('heads.'));

	function suggestId(): string {
		const taken = new Set(Object.keys((getAt(config, base) as Obj | undefined) ?? {}));
		const stem = { camera: 'cam', nozzle: 'N', axis: 'axis', driver: 'driver', actuator: 'act', feeder: 'F', head: 'H', nozzle_tip: 'NT' }[kind] ?? kind;
		for (let i = 1; i < 100; i++) if (!taken.has(`${stem}${i}`)) return `${stem}${i}`;
		return stem;
	}
	let id = $state('');
	$effect(() => {
		void base;
		id = suggestId();
	});

	const ids = (section: string) => refs[section] ?? [];
	const axesOfKind = (k: string) => ids('axes').filter((a) => ((config.axes as Obj)[a] as Obj | undefined)?.kind === k || (k === 'linear' && !((config.axes as Obj)[a] as Obj)?.kind));
	const usedLetters = $derived(Object.values((config.axes as Obj | undefined) ?? {}).map((a) => (a as Obj).letter));

	/** Values the new object starts with: enough to be valid, the rest stays at the type's defaults. */
	function defaults(): Obj {
		const one = (section: string) => (ids(section).length === 1 ? ids(section)[0] : undefined);
		if (kind === 'camera') {
			const d: Obj = { looking: onHead ? 'down' : 'up' };
			if (!onHead) d.location = { x: 0, y: 0, z: 0 };
			else d.offset = { x: 0, y: 0 };
			if (typeName === 'v4l2') d.format = { width: 1280, height: 960, fps: 30, pixel_format: 'MJPG' };
			if (typeName === 'v4l2') d.device = {};
			return d;
		}
		if (kind === 'axis' && typeName === 'controller')
			return { driver: one('drivers') ?? ids('drivers')[0], letter: ['X', 'Y', 'Z', 'A', 'B', 'C'].find((l) => !usedLetters.includes(l)) ?? 'C' };
		if (kind === 'axis' && typeName === 'mapped') return { input: ids('axes')[0], map: [[0, 0], [1, 1]] };
		if (kind === 'actuator' && typeName === 'gcode') return { driver: one('drivers') ?? ids('drivers')[0], on: '', off: '' };
		if (kind === 'nozzle') return { axes: { z: axesOfKind('linear').find((a) => a.startsWith('z')) ?? axesOfKind('linear')[0], rotation: axesOfKind('rotation')[0] }, offset: { x: 0, y: 0 } };
		if (kind === 'head') return { axes: { x: ids('axes').find((a) => a === 'x'), y: ids('axes').find((a) => a === 'y') } };
		if (kind === 'driver' && typeName === 'gcode') return { port: '/dev/serial0', profile: 'grblhal' };
		if (kind === 'feeder') {
			const xyz = { x: 0, y: 0, z: 0 };
			if (typeName === 'strip') return { first: xyz, last: { x: 0, y: 0 } };
			if (typeName === 'tray') return { first: xyz, column_step: { x: 0, y: 0 }, row_step: { x: 0, y: 0 } };
			return { pick_location: xyz };
		}
		return {};
	}

	/** The few settings asked before creating. */
	const setup = $derived.by<FieldSpec[]>(() => {
		if (kind === 'camera') return [{ path: 'looking', label: 'Looking', widget: 'select', options: [['up', 'Up: at the nozzles'], ['down', 'Down: at the bed']] }];
		if (kind === 'axis' && typeName === 'controller')
			return [
				{ path: 'driver', label: 'Driver', widget: 'ref', ref: 'drivers' },
				{ path: 'letter', label: 'Axis letter', widget: 'select', options: ['X', 'Y', 'Z', 'A', 'B', 'C', 'U', 'V', 'W'] }
			];
		if (kind === 'axis' && typeName === 'mapped') return [{ path: 'input', label: 'Input axis', widget: 'ref', ref: 'axes' }];
		if (kind === 'actuator' && typeName === 'gcode') return [{ path: 'driver', label: 'Driver', widget: 'ref', ref: 'drivers' }];
		if (kind === 'nozzle')
			return [
				{ path: 'axes.z', label: 'Z axis', widget: 'ref', ref: 'axes' },
				{ path: 'axes.rotation', label: 'Rotation axis', widget: 'ref', ref: 'axes' }
			];
		if (kind === 'head')
			return [
				{ path: 'axes.x', label: 'X axis', widget: 'ref', ref: 'axes' },
				{ path: 'axes.y', label: 'Y axis', widget: 'ref', ref: 'axes' }
			];
		if (kind === 'driver' && typeName === 'gcode') return [{ path: 'port', label: 'Port', help: 'Serial device, tcp://host:port, or sim://grblhal' }];
		return [];
	});

	let value = $state<Obj>({});
	$effect(() => {
		void typeName;
		void base;
		value = defaults();
	});

	const idError = $derived(
		!/^[A-Za-z_][A-Za-z0-9_-]*$/.test(id)
			? 'Letters, digits, - or _, starting with a letter'
			: Object.keys((getAt(config, base) as Obj | undefined) ?? {}).includes(id)
				? `${id} already exists`
				: null
	);

	let busy = $state(false);
	let error = $state<{ message: string; problems: Problem[] } | null>(null);

	async function accept() {
		if (idError || !type) return;
		busy = true;
		error = null;
		const v: Obj = { ...value };
		if (types.length > 1) v.type = typeName; // a kind with one type leaves it out, as the examples do
		error = await oncreate(`${base}.${id}`, v);
		busy = false;
	}
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && oncancel()} />

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-[10vh]" onclick={(e) => e.target === e.currentTarget && oncancel()}>
	<div class="flex w-full max-w-xl flex-col gap-4 rounded-xl border bg-card p-5 shadow-xl" role="dialog" aria-modal="true" aria-labelledby="new-title">
		<div class="flex items-center gap-2">
			<h2 id="new-title" class="text-base font-semibold">New {label}</h2>
			<Button variant="ghost" size="icon-sm" class="ml-auto" onclick={oncancel} title="Cancel"><X /></Button>
		</div>

		{#if kind === 'camera' && heads.length}
			<div class="flex flex-col gap-1.5">
				<span class="text-xs font-medium text-muted-foreground uppercase">Mounted on</span>
				<div class="grid gap-1.5 sm:grid-cols-2">
					<label class="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-sm has-checked:border-primary has-checked:bg-primary/5">
						<input type="radio" class="mt-1 accent-primary" bind:group={base} value="cameras" />
						<span><span class="font-medium">The machine</span><br /><span class="text-xs text-muted-foreground">Fixed, e.g. looking up at the nozzles</span></span>
					</label>
					{#each heads as h (h)}
						<label class="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-sm has-checked:border-primary has-checked:bg-primary/5">
							<input type="radio" class="mt-1 accent-primary" bind:group={base} value="heads.{h}.cameras" />
							<span><span class="font-medium">Head <span class="font-mono">{h}</span></span><br /><span class="text-xs text-muted-foreground">Moves with it, e.g. looking down</span></span>
						</label>
					{/each}
				</div>
			</div>
		{:else if kind === 'nozzle' && heads.length > 1}
			<label class="flex items-center gap-2 text-sm">
				Head
				<select class="h-8 rounded-md border border-input bg-background px-2 font-mono text-xs" bind:value={base}>
					{#each heads as h (h)}<option value="heads.{h}.nozzles">{h}</option>{/each}
				</select>
			</label>
		{/if}

		{#if types.length > 1}
			<div class="flex flex-col gap-1.5">
				<span class="text-xs font-medium text-muted-foreground uppercase">Type</span>
				<div class="flex max-h-72 flex-col gap-1.5 overflow-y-auto">
					{#each types as t (t.type)}
						<label class="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 text-sm has-checked:border-primary has-checked:bg-primary/5">
							<input type="radio" class="mt-1 accent-primary" bind:group={typeName} value={t.type} />
							<span class="min-w-0">
								<span class="font-medium">{t.title}</span>
								<span class="ml-1 font-mono text-[11px] text-muted-foreground">{t.type}</span>
								{#if t.plugin}<span class="ml-1 text-[11px] text-primary">plugin {t.plugin}</span>{/if}
								{#if t.description}<br /><span class="text-xs text-muted-foreground">{t.description}</span>{/if}
							</span>
						</label>
					{/each}
				</div>
			</div>
		{/if}

		<div class="flex flex-col">
			<div class="grid items-start gap-x-4 gap-y-1 py-1.5 sm:grid-cols-[12rem_1fr]">
				<label for="new-id" class="pt-1.5 text-sm">Id</label>
				<div class="flex flex-col gap-1">
					<input id="new-id" class="h-8 rounded-md border border-input bg-background px-2 font-mono text-xs" bind:value={id} />
					<p class="text-[11px] {idError ? 'text-alarm' : 'text-muted-foreground'}">{idError ?? 'Other settings refer to it by this id.'}</p>
				</div>
			</div>
			{#each setup as f (f.path)}
				<LayoutField spec={f} schema={schemaAt(type?.schema, type?.schema.$defs ?? {}, f.path)} value={getAt(value, f.path)} ids={f.ref ? ids(f.ref) : []} onchange={(v) => (value = setAt(value, f.path, v))} />
			{/each}
		</div>

		<p class="text-xs text-muted-foreground">Everything else starts at its default; set it up on the {label}'s tabs after creating it.</p>

		{#if error}
			<Alert.Root variant="destructive">
				<CircleAlert />
				<Alert.Title>{error.message}</Alert.Title>
				{#if error.problems.length}
					<Alert.Description>
						<ul class="mt-1 list-disc pl-4">
							{#each error.problems as p (p.path + p.message)}<li><span class="font-mono">{p.path}</span>: {p.message}</li>{/each}
						</ul>
					</Alert.Description>
				{/if}
			</Alert.Root>
		{/if}

		<div class="flex justify-end gap-2">
			<Button variant="ghost" onclick={oncancel}>Cancel</Button>
			<Button disabled={busy || !!idError || !type} onclick={accept}><Plus /> Create {label}</Button>
		</div>
	</div>
</div>
