<script lang="ts">
	// One tab of a hand-made settings page: its titled groups of labelled fields and their buttons.
	import type { GroupSpec, LayoutContext } from '#lib/config/layouts.js';
	import { getAt, setAt } from '#lib/config/layouts.js';
	import { schemaAt, type Defs, type Schema } from '#lib/config/schema.js';
	import LayoutField from './LayoutField.svelte';
	import ConfigAction from './ConfigAction.svelte';

	type Obj = Record<string, unknown>;

	let {
		groups,
		ctx,
		schema,
		defs,
		value = $bindable(),
		config,
		refs,
		saved
	}: {
		groups: GroupSpec[];
		ctx: LayoutContext;
		schema: Schema | undefined;
		defs: Defs;
		value: Obj;
		config: Obj;
		refs: Record<string, string[]>;
		saved: boolean;
	} = $props();

	// shown above the fields: they fill them in, or explain them
	const FIRST = ['device-picker', 'head-mounted'];
	const set = (path: string, v: unknown) => (value = setAt(value, path, v));
</script>

<div class="flex flex-col gap-4">
	{#each groups as g (g.title)}
		<fieldset class="rounded-lg border px-4 pt-1 pb-3">
			<legend class="px-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{g.title}</legend>
			{#if g.note}<p class="mb-2 text-xs text-muted-foreground">{g.note}</p>{/if}
			{#each (g.actions ?? []).filter((a) => FIRST.includes(a)) as a (a)}
				<div class="mb-2"><ConfigAction action={a} {ctx} {config} {saved} {set} /></div>
			{/each}
			{#if g.inline}
				<div class="flex flex-wrap items-end gap-3">
					{#each g.fields as f (f.path)}
						<LayoutField spec={f} schema={schemaAt(schema, defs, f.path)} value={getAt(value, f.path)} ids={f.ref ? refs[f.ref] : []} inline onchange={(v) => set(f.path, v)} />
					{/each}
				</div>
			{:else}
				<div class="flex flex-col divide-y divide-border/50">
					{#each g.fields as f (f.path)}
						<LayoutField spec={f} schema={schemaAt(schema, defs, f.path)} value={getAt(value, f.path)} ids={f.ref ? refs[f.ref] : []} onchange={(v) => set(f.path, v)} />
					{/each}
				</div>
			{/if}
			{#each (g.actions ?? []).filter((a) => !FIRST.includes(a)) as a (a)}
				<div class="mt-2"><ConfigAction action={a} {ctx} {config} {saved} {set} /></div>
			{/each}
		</fieldset>
	{/each}
</div>
