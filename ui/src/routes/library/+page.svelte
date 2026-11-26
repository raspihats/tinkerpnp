<script lang="ts">
	import * as Alert from '#lib/components/ui/alert/index.js';
	// Library: parts, packages and boards (and the KiCad import for boards). Items are edited as JSON for now.
	import { tinker, RpcError } from '#lib/api/client.svelte.js';
	import { segmentItem } from '#lib/styles.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Search, Plus, Save, Trash2, FileUp, Library, CircleAlert } from '@lucide/svelte';

	type Item = { id: string; name?: string; revision: number; [k: string]: unknown };
	const COLLECTIONS = [
		{ id: 'parts', label: 'Parts', blank: { id: '', name: '', package: '', height: 0 } },
		{ id: 'packages', label: 'Packages', blank: { id: '', description: '', footprint: { body_width: 0, body_height: 0, pads: [] } } },
		{ id: 'boards', label: 'Boards', blank: { id: '', name: '', placements: [] } }
	] as const;

	let collection = $state<string>('parts');
	let filter = $state('');
	let items = $state<Item[]>([]);
	let total = $state(0);
	let selected = $state<Item | null>(null);
	let text = $state('');
	let original = $state('');
	let problems = $state<{ path: string; message: string }[]>([]);
	let creating = $state(false);

	// KiCad import
	let kicadId = $state('');
	let kicadFile = $state<File | null>(null);
	let kicadReplace = $state(false);

	async function reload() {
		const r = await tinker.call<{ items: Item[]; total: number }>(`data.${collection}.list`, { filter: filter || undefined, limit: 1000 });
		items = r.items;
		total = r.total;
		if (selected && !creating) {
			const fresh = items.find((i) => i.id === selected!.id);
			if (fresh && text === original) open(fresh);
		}
	}

	$effect(() => {
		void collection;
		void filter;
		if (tinker.connection !== 'connected') return;
		reload().catch(() => {});
	});
	$effect(() => {
		if (tinker.connection !== 'connected') return;
		return tinker.on('notify_data_changed', (p) => {
			if (p.collection === collection) reload().catch(() => {});
		});
	});
	$effect(() => {
		void collection;
		selected = null;
		creating = false;
	});

	function shown(item: Item): Record<string, unknown> {
		const { revision: _r, updated: _u, ...rest } = item;
		return rest;
	}

	function open(item: Item) {
		selected = item;
		creating = false;
		original = JSON.stringify(shown(item), null, 2);
		text = original;
		problems = [];
	}

	function startNew() {
		const blank = COLLECTIONS.find((c) => c.id === collection)!.blank;
		selected = null;
		creating = true;
		original = '';
		text = JSON.stringify(blank, null, 2);
		problems = [];
	}

	async function save() {
		let item: Record<string, unknown>;
		try {
			item = JSON.parse(text);
		} catch (e) {
			problems = [{ path: '', message: `not valid JSON: ${(e as Error).message}` }];
			return;
		}
		problems = [];
		try {
			const r = creating
				? await tinker.call<Item>(`data.${collection}.create`, { item })
				: await tinker.call<Item>(`data.${collection}.update`, { id: selected!.id, revision: selected!.revision, changes: item });
			tinker.notify('ok', `${r.id} saved`);
			creating = false;
			await reload();
			open(r);
		} catch (e) {
			if (e instanceof RpcError) {
				problems = (e.data.errors as { path: string; message: string }[] | undefined) ?? [{ path: '', message: e.message }];
			} else problems = [{ path: '', message: String(e) }];
		}
	}

	async function remove() {
		if (!selected) return;
		const r = await tinker.act(`data.${collection}.delete`, { id: selected.id, revision: selected.revision });
		if (r) {
			tinker.notify('ok', `${selected.id} deleted`);
			selected = null;
			await reload();
		}
	}

	async function importKicad() {
		if (!kicadFile || !kicadId.trim()) return;
		const textIn = await kicadFile.text();
		const r = await tinker.act<{ placements: number; fiducials: number; bottom: number; created_parts: string[] }>('import.kicad_pos', {
			text: textIn,
			board_id: kicadId.trim(),
			replace: kicadReplace
		});
		if (r) {
			tinker.notify('ok', `Board ${kicadId}: ${r.placements} placements (${r.fiducials} fiducials, ${r.bottom} bottom), ${r.created_parts.length} new parts`);
			collection = 'boards';
			kicadFile = null;
		}
	}
</script>

<div class="mb-4 flex flex-wrap items-center gap-3">
	<ToggleGroup.Root type="single" variant="outline" size="sm" value={collection} onValueChange={(v) => v && (collection = v)}>
		{#each COLLECTIONS as c (c.id)}<ToggleGroup.Item value={c.id} class={segmentItem}>{c.label}</ToggleGroup.Item>{/each}
	</ToggleGroup.Root>
	<div class="relative w-full max-w-xs">
		<Search class="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
		<Input class="pl-8" placeholder="Filter…" bind:value={filter} />
	</div>
	<span class="ml-auto text-xs text-muted-foreground">{total} {collection}</span>
	<Button size="sm" variant="outline" onclick={startNew}><Plus /> New</Button>
</div>

<div class="grid items-start gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
	<Card.Root size="sm" class="gap-0 py-0">
		<div class="max-h-[70vh] overflow-y-auto">
			{#each items as it (it.id)}
				<button
					class="flex w-full items-center gap-2 border-b px-3 py-2 text-left last:border-0 hover:bg-accent {selected?.id === it.id ? 'bg-primary/10' : ''}"
					onclick={() => open(it)}
				>
					<span class="truncate font-mono text-xs {selected?.id === it.id ? 'font-semibold text-primary' : ''}">{it.id}</span>
					{#if collection === 'boards'}<Badge variant="outline" class="ml-auto">{(it.placements as unknown[]).length}</Badge>
					{:else if collection === 'parts' && it.package}<span class="ml-auto truncate text-[11px] text-muted-foreground">{it.package}</span>{/if}
				</button>
			{:else}
				<p class="p-4 text-sm text-muted-foreground">Nothing here yet.</p>
			{/each}
		</div>
	</Card.Root>

	<div class="flex flex-col gap-4">
		{#if selected || creating}
			<Card.Root class="gap-3">
				<Card.Header class="flex items-center gap-2">
					<Card.Title class="font-mono text-sm">{creating ? `new ${collection.slice(0, -1)}` : selected?.id}</Card.Title>
					{#if selected}<Badge variant="outline" class="font-mono">rev {selected.revision}</Badge>{/if}
					<Card.Action class="flex gap-1">
						{#if selected}<Button variant="ghost" size="sm" class="text-alarm" onclick={remove}><Trash2 /> Delete</Button>{/if}
						<Button size="sm" disabled={!creating && text === original} onclick={save}><Save /> {creating ? 'Create' : 'Save'}</Button>
					</Card.Action>
				</Card.Header>
				<Card.Content class="flex flex-col gap-2">
					<textarea class="min-h-96 w-full rounded-lg border border-input bg-well p-3 font-mono text-xs leading-5 outline-none focus-visible:ring-2 focus-visible:ring-ring/50" spellcheck="false" bind:value={text}></textarea>
					{#if problems.length}
						<Alert.Root variant="destructive">
							<CircleAlert />
							<Alert.Title>Not saved: {problems.length} problem{problems.length === 1 ? '' : 's'}</Alert.Title>
							<Alert.Description>
								<ul class="mt-1 list-disc pl-4">
									{#each problems as p (p.path + p.message)}<li><span class="font-mono">{p.path}</span> {p.message}</li>{/each}
								</ul>
							</Alert.Description>
						</Alert.Root>
					{/if}
					<p class="text-xs text-muted-foreground">Lengths in mm (or with a unit, e.g. <code>"0.5 mm"</code>), angles in degrees.</p>
				</Card.Content>
			</Card.Root>
		{:else}
			<Card.Root><Card.Content class="flex flex-col items-center gap-2 py-10 text-center text-sm text-muted-foreground"><Library class="size-6" />Pick an item on the left, or create one.</Card.Content></Card.Root>
		{/if}

		<Card.Root class="gap-3">
			<Card.Header class="flex items-center gap-2"><FileUp class="size-4 text-muted-foreground" /><Card.Title class="text-sm">Import a board from KiCad</Card.Title></Card.Header>
			<Card.Content class="flex flex-col gap-2 text-sm">
				<p class="text-xs text-muted-foreground">
					KiCad: File → Fabrication Outputs → Component Placement (.pos, ASCII or CSV, mm), with the drill/place origin at the
					board's lower left corner. Parts are named Package-Value; missing parts and packages are created.
				</p>
				<div class="flex flex-wrap items-center gap-2">
					<Input type="file" accept=".pos,.csv,.txt" class="h-9 max-w-xs text-xs" onchange={(e) => (kicadFile = (e.target as HTMLInputElement).files?.[0] ?? null)} />
					<Input class="h-9 w-40 font-mono text-xs" placeholder="board id" bind:value={kicadId} />
					<label class="flex items-center gap-1.5 text-xs"><input type="checkbox" bind:checked={kicadReplace} /> replace</label>
					<Button size="sm" disabled={!kicadFile || !kicadId.trim()} onclick={importKicad}><FileUp /> Import</Button>
				</div>
			</Card.Content>
		</Card.Root>
	</div>
</div>
