<script lang="ts">
	// A form for one object's settings, built from its type's JSON Schema (config.types). Every change replaces
	// `value` with a new object (also nested ones), so binding works through any depth. An empty field is left out of
	// the configuration: the type's default (shown greyed) applies.
	import Self from './SchemaFields.svelte';
	import DevicePicker from './DevicePicker.svelte';
	import { deref, kindOf, label, parseScalar, refKind, unwrap, type Defs, type Schema } from '#lib/config/schema.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Plus, X } from '@lucide/svelte';

	type Obj = Record<string, unknown>;

	let {
		schema,
		defs,
		value = $bindable(),
		parent = null,
		only = 'all',
		hide = []
	}: {
		schema: Schema;
		defs: Defs;
		value: Obj | undefined;
		parent?: string | null;
		only?: 'all' | 'required' | 'optional';
		hide?: string[];
	} = $props();

	const obj = $derived(deref(schema, defs));
	const required = $derived(obj.required ?? []);
	const fields = $derived(
		Object.entries(obj.properties ?? {}).filter(([name]) => {
			if (hide.includes(name)) return false;
			if (only === 'required') return required.includes(name);
			if (only === 'optional') return !required.includes(name);
			return true;
		})
	);

	let jsonErrors = $state<Record<string, string>>({});

	function setField(name: string, v: unknown) {
		const next: Obj = { ...(value ?? {}) };
		if (v === undefined) delete next[name];
		else next[name] = v;
		value = next;
	}

	const text = (v: unknown) => (v === undefined || v === null ? '' : String(v));
	const placeholder = (s: Schema) => (s.default === undefined || s.default === null ? '' : String(s.default));

	function setJson(name: string, raw: string) {
		if (raw.trim() === '') {
			setField(name, undefined);
			jsonErrors = { ...jsonErrors, [name]: '' };
			return;
		}
		try {
			setField(name, JSON.parse(raw));
			jsonErrors = { ...jsonErrors, [name]: '' };
		} catch (e) {
			jsonErrors = { ...jsonErrors, [name]: `Not valid JSON: ${(e as Error).message}` };
		}
	}

	const inputClass =
		'h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50 placeholder:text-muted-foreground/60';
</script>

<div class="flex flex-col">
	{#each fields as [name, raw] (name)}
		{@const u = unwrap(raw, defs)}
		{@const s = u.schema}
		{@const kind = kindOf(s)}
		{@const req = required.includes(name)}
		{@const v = value?.[name]}
		{@const rk = refKind(name, parent)}
		{#if kind === 'object'}
			<!-- nested settings (location, format, device, ...) -->
			<div class="border-b py-2 last:border-0">
				<div class="flex items-center gap-2">
					<span class="font-mono text-xs font-medium">{name}{#if req}<span class="text-alarm"> *</span>{/if}</span>
					{#if s.description}<span class="truncate text-[11px] text-muted-foreground">{s.description}</span>{/if}
					{#if !req && v !== undefined && v !== null}
						<Button variant="ghost" size="xs" class="ml-auto" title="Remove: back to the default" onclick={() => setField(name, undefined)}><X /> Clear</Button>
					{/if}
				</div>
				{#if v === undefined || v === null}
					{#if req || !u.nullable}
						<div class="mt-1.5 ml-3 border-l pl-3">
							{#if name === 'device' && s.title === 'DeviceSelector'}<div class="mb-2"><DevicePicker onpick={(d) => setField(name, d)} /></div>{/if}
							<Self schema={s} {defs} bind:value={() => undefined, (nv) => setField(name, nv)} parent={name} />
						</div>
					{:else}
						<Button variant="outline" size="xs" class="mt-1" onclick={() => setField(name, {})}><Plus /> Set {label(name)}</Button>
					{/if}
				{:else}
					<div class="mt-1.5 ml-3 border-l pl-3">
						{#if name === 'device' && s.title === 'DeviceSelector'}<div class="mb-2"><DevicePicker onpick={(d) => setField(name, d)} /></div>{/if}
						<Self schema={s} {defs} bind:value={() => v as Obj, (nv) => setField(name, nv)} parent={name} />
					</div>
				{/if}
			</div>
		{:else}
			<div class="grid items-start gap-x-3 gap-y-1 border-b py-1.5 last:border-0 sm:grid-cols-[10rem_1fr]">
				<label for="f-{parent ?? ''}-{name}" class="pt-1.5 font-mono text-xs break-all">
					{name}{#if req}<span class="text-alarm"> *</span>{/if}
				</label>
				<div class="flex min-w-0 flex-col gap-1">
					{#if kind === 'enum'}
						<select id="f-{parent ?? ''}-{name}" class={inputClass} value={text(v)} onchange={(e) => setField(name, e.currentTarget.value === '' ? undefined : e.currentTarget.value)}>
							{#if !req || v === undefined}<option value="">{req ? 'choose…' : `default${placeholder(s) ? `: ${placeholder(s)}` : ''}`}</option>{/if}
							{#each s.enum ?? [] as opt (String(opt))}<option value={String(opt)}>{String(opt)}</option>{/each}
						</select>
					{:else if kind === 'boolean'}
						<select id="f-{parent ?? ''}-{name}" class={inputClass} value={v === undefined ? '' : String(v)} onchange={(e) => setField(name, e.currentTarget.value === '' ? undefined : e.currentTarget.value === 'true')}>
							<option value="">default{placeholder(s) ? `: ${placeholder(s)}` : ''}</option>
							<option value="true">true</option>
							<option value="false">false</option>
						</select>
					{:else if kind === 'range'}
						{@const pair = Array.isArray(v) ? (v as unknown[]) : [undefined, undefined]}
						<div class="flex items-center gap-1.5">
							{#each [0, 1] as i (i)}
								<input
									class={inputClass}
									placeholder={i === 0 ? 'min' : 'max'}
									value={text(pair[i])}
									onchange={(e) => {
										const next = [pair[0], pair[1]];
										next[i] = e.currentTarget.value.trim() === '' ? undefined : parseScalar(e.currentTarget.value, 'number');
										setField(name, next[0] === undefined && next[1] === undefined ? undefined : next);
									}}
								/>
								{#if i === 0}<span class="text-xs text-muted-foreground">to</span>{/if}
							{/each}
						</div>
					{:else if kind === 'strings'}
						<input
							id="f-{parent ?? ''}-{name}"
							class={inputClass}
							placeholder="comma separated"
							value={Array.isArray(v) ? (v as unknown[]).join(', ') : ''}
							onchange={(e) => {
								const items = e.currentTarget.value.split(',').map((x) => x.trim()).filter(Boolean);
								setField(name, items.length ? items : undefined);
							}}
						/>
					{:else if kind === 'json'}
						<textarea
							id="f-{parent ?? ''}-{name}"
							class="min-h-16 w-full rounded-md border border-input bg-background p-2 font-mono text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
							spellcheck="false"
							placeholder="JSON"
							value={v === undefined ? '' : JSON.stringify(v, null, 2)}
							onchange={(e) => setJson(name, e.currentTarget.value)}
						></textarea>
						{#if jsonErrors[name]}<p class="text-[11px] text-alarm">{jsonErrors[name]}</p>{/if}
					{:else}
						<input
							id="f-{parent ?? ''}-{name}"
							class={inputClass}
							placeholder={placeholder(s)}
							list={rk ? `ref-${rk}` : undefined}
							value={text(v)}
							onchange={(e) => setField(name, e.currentTarget.value.trim() === '' ? undefined : parseScalar(e.currentTarget.value, kind))}
						/>
					{/if}
					{#if s.description}<p class="text-[11px] leading-snug text-muted-foreground">{s.description}</p>{/if}
				</div>
			</div>
		{/if}
	{/each}
	{#if !fields.length}<p class="py-1 text-xs text-muted-foreground">No settings.</p>{/if}
</div>
