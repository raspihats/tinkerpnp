<script lang="ts">
	// One labelled setting of a hand-made settings page. An empty input removes the setting, so the type's default
	// (shown greyed) applies. Numbers may carry units ("8000 mm/min"): they are kept as text then.
	import type { FieldSpec } from '#lib/config/layouts.js';
	import { parseScalar, type Schema } from '#lib/config/schema.js';
	import { Plus, X } from '@lucide/svelte';

	let {
		spec,
		schema,
		value,
		ids = [],
		inline = false,
		onchange
	}: {
		spec: FieldSpec;
		schema: Schema;
		value: unknown;
		/** ids to choose from (ref / refs) */
		ids?: string[];
		inline?: boolean;
		onchange: (v: unknown) => void;
	} = $props();

	const widget = $derived(spec.widget ?? (schema.enum ? 'select' : schema.type === 'boolean' ? 'bool' : 'text'));
	const help = $derived(spec.help ?? schema.description);
	const def = $derived(schema.default === undefined || schema.default === null ? '' : typeof schema.default === 'object' ? '' : String(schema.default));
	const placeholder = $derived(spec.placeholder ?? def);
	const options = $derived<[string, string][]>(
		(spec.options ?? (schema.enum as (string | number)[] | undefined) ?? []).map((o) => (Array.isArray(o) ? [String(o[0]), o[1]] : [String(o), String(o)]))
	);
	const id = $derived(`f-${spec.path}`);
	const text = (v: unknown) => (v === undefined || v === null ? '' : String(v));

	let jsonError = $state('');
	function setJson(raw: string) {
		if (raw.trim() === '') {
			jsonError = '';
			onchange(undefined);
			return;
		}
		try {
			onchange(JSON.parse(raw));
			jsonError = '';
		} catch (e) {
			jsonError = `Not valid JSON: ${(e as Error).message}`;
		}
	}

	function fromSelect(raw: string) {
		if (raw === '') return onchange(undefined);
		// keep numbers numbers (rotate: 90)
		const opt = (spec.options ?? schema.enum ?? []).map((o) => (Array.isArray(o) ? o[0] : o)).find((o) => String(o) === raw);
		onchange(opt ?? raw);
	}

	// commands: name -> template rows
	const commands = $derived(Object.entries((value as Record<string, unknown> | undefined) ?? {}));
	let newCommand = $state('');
	function setCommand(name: string, v: string | undefined) {
		const next = { ...((value as Record<string, unknown> | undefined) ?? {}) };
		if (v === undefined) delete next[name];
		else next[name] = v;
		onchange(Object.keys(next).length ? next : undefined);
	}

	const input =
		'h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50 placeholder:text-muted-foreground/50 disabled:opacity-60';
	const mono = 'font-mono text-xs';
</script>

{#snippet control()}
	{#if widget === 'textarea'}
		<textarea {id} class="{input} h-auto min-h-16 py-1.5" value={text(value)} onchange={(e) => onchange(e.currentTarget.value.trim() === '' ? undefined : e.currentTarget.value)}></textarea>
	{:else if widget === 'select'}
		<select {id} class={input} value={text(value)} onchange={(e) => fromSelect(e.currentTarget.value)}>
			<option value="">{def ? `Default (${options.find((o) => o[0] === def)?.[1] ?? def})` : '—'}</option>
			{#each options as [v, l] (v)}<option value={v}>{l}</option>{/each}
			{#if value !== undefined && value !== null && !options.some((o) => o[0] === text(value))}<option value={text(value)}>{text(value)}</option>{/if}
		</select>
	{:else if widget === 'bool'}
		<select {id} class={input} value={value === undefined || value === null ? '' : String(value)} onchange={(e) => onchange(e.currentTarget.value === '' ? undefined : e.currentTarget.value === 'true')}>
			<option value="">Default{def ? ` (${def === 'true' ? 'yes' : 'no'})` : ''}</option>
			<option value="true">Yes</option>
			<option value="false">No</option>
		</select>
	{:else if widget === 'ref'}
		<select {id} class="{input} {mono}" value={text(value)} onchange={(e) => onchange(e.currentTarget.value === '' ? undefined : e.currentTarget.value)}>
			<option value="">— none —</option>
			{#each ids as i (i)}<option value={i}>{i}</option>{/each}
			{#if value && !ids.includes(String(value))}<option value={text(value)}>{text(value)} (missing)</option>{/if}
		</select>
	{:else if widget === 'refs'}
		{@const chosen = Array.isArray(value) ? (value as string[]) : []}
		<div class="flex min-h-8 flex-wrap items-center gap-x-3 gap-y-1">
			{#each [...new Set([...ids, ...chosen])] as i (i)}
				<label class="flex items-center gap-1.5 font-mono text-xs">
					<input
						type="checkbox"
						class="accent-primary"
						checked={chosen.includes(i)}
						onchange={(e) => {
							const next = e.currentTarget.checked ? [...chosen, i] : chosen.filter((x) => x !== i);
							onchange(next.length ? next : undefined);
						}}
					/>{i}{#if !ids.includes(i)}<span class="text-alarm"> (missing)</span>{/if}
				</label>
			{:else}
				<span class="text-xs text-muted-foreground">None configured.</span>
			{/each}
		</div>
	{:else if widget === 'range'}
		{@const pair = Array.isArray(value) ? (value as unknown[]) : [undefined, undefined]}
		<div class="flex items-center gap-1.5">
			{#each [0, 1] as i (i)}
				<input
					id={i === 0 ? id : undefined}
					class="{input} {mono}"
					placeholder={i === 0 ? 'from' : 'to'}
					value={text(pair[i])}
					onchange={(e) => {
						const next = [pair[0], pair[1]];
						next[i] = e.currentTarget.value.trim() === '' ? undefined : parseScalar(e.currentTarget.value, 'number');
						onchange(next[0] === undefined && next[1] === undefined ? undefined : next);
					}}
				/>
				{#if i === 0}<span class="text-xs text-muted-foreground">to</span>{/if}
			{/each}
			{#if spec.unit}<span class="shrink-0 text-xs text-muted-foreground">{spec.unit}</span>{/if}
		</div>
	{:else if widget === 'list' || widget === 'numbers'}
		<input
			{id}
			class="{input} {mono}"
			placeholder={Array.isArray(schema.default) ? (schema.default as unknown[]).join(', ') : 'comma separated'}
			value={Array.isArray(value) ? (value as unknown[]).join(', ') : ''}
			onchange={(e) => {
				const items = e.currentTarget.value.split(',').map((x) => x.trim()).filter(Boolean);
				onchange(items.length ? (widget === 'numbers' ? items.map((x) => parseScalar(x, 'number')) : items) : undefined);
			}}
		/>
	{:else if widget === 'json'}
		<textarea
			{id}
			class="{input} {mono} h-auto min-h-14 py-1.5"
			spellcheck="false"
			placeholder={schema.default !== undefined && typeof schema.default === 'object' ? JSON.stringify(schema.default) : 'JSON'}
			value={value === undefined ? '' : JSON.stringify(value, null, Array.isArray(value) && JSON.stringify(value).length < 60 ? undefined : 2)}
			onchange={(e) => setJson(e.currentTarget.value)}
		></textarea>
		{#if jsonError}<p class="text-[11px] text-alarm">{jsonError}</p>{/if}
	{:else if widget === 'commands'}
		<div class="flex flex-col gap-1">
			{#each commands as [name, tpl] (name)}
				<div class="grid grid-cols-[minmax(8rem,14rem)_1fr_auto] items-center gap-1.5">
					<span class="truncate font-mono text-[11px] text-muted-foreground" title={name}>{name}</span>
					<input class="{input} {mono}" value={typeof tpl === 'string' ? tpl : JSON.stringify(tpl)} onchange={(e) => setCommand(name, e.currentTarget.value)} />
					<button class="rounded p-1 text-muted-foreground hover:text-alarm" title="Remove: back to the profile's command" onclick={() => setCommand(name, undefined)}><X class="size-3.5" /></button>
				</div>
			{:else}
				<p class="text-xs text-muted-foreground">The profile's commands are used.</p>
			{/each}
			<div class="flex items-center gap-1.5 pt-1">
				<input class="{input} {mono} max-w-64" placeholder="COMMAND_NAME" bind:value={newCommand} />
				<button
					class="flex h-8 items-center gap-1 rounded-md border px-2 text-xs hover:bg-accent disabled:opacity-50"
					disabled={!/^[A-Z][A-Z0-9_]*$/.test(newCommand) || commands.some(([n]) => n === newCommand)}
					onclick={() => {
						setCommand(newCommand, '');
						newCommand = '';
					}}><Plus class="size-3.5" /> Override</button
				>
			</div>
		</div>
	{:else}
		{@const numeric = widget === 'number' || widget === 'integer'}
		<div class="flex items-center gap-1.5">
			<input
				{id}
				class="{input} {numeric ? `${mono} text-right` : ''} {spec.narrow || inline ? 'max-w-32' : ''}"
				{placeholder}
				value={text(value)}
				onchange={(e) => onchange(e.currentTarget.value.trim() === '' ? undefined : numeric ? parseScalar(e.currentTarget.value, 'number') : e.currentTarget.value)}
			/>
			{#if spec.unit}<span class="shrink-0 text-xs text-muted-foreground">{spec.unit}</span>{/if}
		</div>
	{/if}
{/snippet}

{#if inline}
	<div class="flex flex-col gap-1">
		<label for={id} class="text-xs text-muted-foreground">{spec.label}</label>
		{@render control()}
	</div>
{:else}
	<div class="grid items-start gap-x-4 gap-y-1 py-1.5 sm:grid-cols-[12rem_1fr]">
		<label for={id} class="pt-1.5 text-sm">{spec.label}</label>
		<div class="flex min-w-0 flex-col gap-1">
			{@render control()}
			{#if help}<p class="text-[11px] leading-snug text-muted-foreground">{help}</p>{/if}
		</div>
	</div>
{/if}
