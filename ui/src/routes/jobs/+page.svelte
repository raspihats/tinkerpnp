<script lang="ts">
	// Jobs: which boards go where on the machine, and running them (load, start, pause, step, skip, stop).
	import { tinker, fmt, RpcError } from '#lib/api/client.svelte.js';
	import { segmentItem } from '#lib/styles.js';
	import { stateVariant } from '#lib/status.js';
	import * as Card from '#lib/components/ui/card/index.js';
	import * as Table from '#lib/components/ui/table/index.js';
	import * as ToggleGroup from '#lib/components/ui/toggle-group/index.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Input } from '#lib/components/ui/input/index.js';
	import { Switch } from '#lib/components/ui/switch/index.js';
	import { Label } from '#lib/components/ui/label/index.js';
	import { Play, Pause, StepForward, Square, SkipForward, RotateCcw, Upload, Plus, Trash2, Crosshair, ListChecks, Save, FlaskConical, Check, ScanSearch, LoaderCircle } from '@lucide/svelte';

	type JobBoard = { board: string; location: { x: number; y: number; z: number; rotation: number }; side: string; enabled: boolean; check_fiducials: boolean; placed: string[] };
	type Job = {
		id: string; name?: string; boards: JobBoard[]; revision: number;
		order?: string; error_handling?: string; max_placement_retries?: number;
	};
	type PlanStep = { board: string; placement: string; part: string | null; location: { x: number; y: number; rotation: number }; done: boolean };

	let jobs = $state<Job[]>([]);
	let boards = $state<{ id: string; name?: string; placements: unknown[] }[]>([]);
	let selected = $state<string>('');
	let draft = $state<Job | null>(null);
	let dirty = $state(false);
	let plan = $state<PlanStep[]>([]);
	let newId = $state('');
	let dryRun = $state(false);

	const job = $derived(tinker.status['job'] ?? {});
	const progress = $derived((job.progress ?? { done: 0, total: 0 }) as { done: number; total: number });
	const loadedHere = $derived(job.job_id === selected);
	const running = $derived(job.state === 'running');
	const paused = $derived(job.state === 'paused');

	async function reload() {
		const [j, b] = await Promise.all([
			tinker.call<{ items: Job[] }>('data.jobs.list'),
			tinker.call<{ items: { id: string; name?: string; placements: unknown[] }[] }>('data.boards.list')
		]);
		jobs = j.items;
		boards = b.items;
		if (!selected && jobs.length) selected = jobs[0].id;
		if (!dirty) draft = structuredClone($state.snapshot(jobs.find((x) => x.id === selected) ?? null));
	}

	$effect(() => {
		if (tinker.connection !== 'connected') return;
		reload().catch(() => {});
		return tinker.on('notify_data_changed', (p) => {
			if (p.collection === 'jobs' || p.collection === 'boards') reload().catch(() => {});
			if (p.collection === 'jobs' && loadedHere) loadPlan();
		});
	});

	$effect(() => {
		void selected;
		dirty = false;
		draft = structuredClone($state.snapshot(jobs.find((x) => x.id === selected) ?? null));
		plan = [];
	});

	// progress events: refresh the plan's done marks
	$effect(() => {
		void progress.done;
		if (loadedHere) loadPlan();
	});

	async function loadPlan() {
		try {
			plan = await tinker.call<PlanStep[]>('job.plan');
		} catch {
			plan = [];
		}
	}

	async function create() {
		const id = newId.trim();
		if (!id) return;
		const r = await tinker.act('data.jobs.create', { item: { id, name: id, boards: [] } });
		if (r) {
			newId = '';
			selected = id;
			await reload();
		}
	}

	async function save() {
		if (!draft) return;
		const r = await tinker.act<Job>('data.jobs.update', { id: draft.id, revision: draft.revision, changes: { name: draft.name, boards: draft.boards, order: draft.order ?? 'nozzle_tips', error_handling: draft.error_handling ?? 'alert', max_placement_retries: draft.max_placement_retries ?? 5 } });
		if (r) {
			dirty = false;
			tinker.notify('ok', `Job ${draft.id} saved`);
			await reload();
		}
	}

	function addBoard(id: string) {
		if (!draft || !id) return;
		draft.boards.push({ board: id, location: { x: 0, y: 0, z: 0, rotation: 0 }, side: 'top', enabled: true, check_fiducials: false, placed: [] });
		dirty = true;
	}

	function fromCamera(i: number) {
		// the board origin where the down-looking head camera is now (put the crosshair on the board's origin)
		const cam = Object.entries(tinker.status).find(([n, s]) => n.startsWith('camera/') && s.looking === 'down');
		const loc = cam?.[1].location as { x: number; y: number } | undefined;
		if (!draft || !loc) return tinker.notify('warn', 'No down-looking camera position');
		draft.boards[i].location.x = Math.round(loc.x * 1000) / 1000;
		draft.boards[i].location.y = Math.round(loc.y * 1000) / 1000;
		dirty = true;
	}

	async function load() {
		const r = await tinker.act<{ placements: number }>('job.load', { job_id: selected });
		if (r) {
			tinker.notify('info', `Job ${selected}: ${r.placements} placements to do`);
			await loadPlan();
		}
	}
	const run = (method: string, params: Record<string, unknown> = {}) => tinker.act(method, params);

	let checking = $state<number | null>(null);
	async function checkFiducials(i: number) {
		if (!draft || dirty) return tinker.notify('warn', 'Save the job first');
		checking = i;
		const fit = await tinker.act<{ x: number; y: number; rotation: number; error_mm: number; fiducials: { id: string; off: number }[] }>(
			'job.check_fiducials',
			{ job_id: draft.id, board_index: i, save: true },
			180000
		);
		checking = null;
		if (fit) {
			tinker.notify('ok', `Board ${i + 1}: X ${fit.x.toFixed(3)} Y ${fit.y.toFixed(3)} R ${fit.rotation.toFixed(3)}° (fit error ${fit.error_mm} mm; ` +
				fit.fiducials.map((f) => `${f.id} off ${f.off} mm`).join(', ') + ')');
			await reload();
		}
	}
</script>

<div class="grid items-start gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
	<Card.Root size="sm" class="gap-2">
		<Card.Header class="flex items-center gap-2"><ListChecks class="size-4 text-muted-foreground" /><Card.Title class="text-sm">Jobs</Card.Title></Card.Header>
		<Card.Content class="flex flex-col gap-1">
			{#each jobs as j (j.id)}
				<button
					class="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent {selected === j.id ? 'bg-primary/15 font-semibold text-primary' : ''}"
					onclick={() => (selected = j.id)}
				>
					<span class="truncate">{j.name ?? j.id}</span>
					<span class="ml-auto text-xs text-muted-foreground">{j.boards.length} board{j.boards.length === 1 ? '' : 's'}</span>
				</button>
			{:else}
				<p class="px-2 text-xs text-muted-foreground">No jobs yet.</p>
			{/each}
			<form class="mt-2 flex gap-1" onsubmit={(e) => { e.preventDefault(); create(); }}>
				<Input class="h-8 text-xs" placeholder="new job id" bind:value={newId} />
				<Button type="submit" size="icon" variant="outline" title="New job"><Plus /></Button>
			</form>
		</Card.Content>
	</Card.Root>

	{#if draft}
		<div class="flex flex-col gap-4">
			<!-- run -->
			<Card.Root class="gap-3">
				<Card.Header class="flex flex-wrap items-center gap-2">
					<Card.Title class="text-base">{draft.name ?? draft.id}</Card.Title>
					{#if loadedHere}<Badge variant={stateVariant(job.state === 'finished' ? 'idle' : job.state)} class="capitalize">{job.state}</Badge>{/if}
					{#if loadedHere && job.message}<span class="text-xs text-muted-foreground">{job.message}</span>{/if}
				</Card.Header>
				<Card.Content class="flex flex-col gap-3">
					<div class="flex flex-wrap items-center gap-2">
						<Button variant="outline" onclick={load} disabled={running || dirty} title={dirty ? 'Save the job first' : 'Plan the placements'}><Upload /> Load</Button>
						<Button onclick={() => run('job.start', { dry_run: dryRun })} disabled={!loadedHere || running || paused}><Play /> Start</Button>
						<Button variant="secondary" onclick={() => run('job.pause')} disabled={!running}><Pause /> Pause</Button>
						<Button variant="secondary" onclick={() => run('job.resume')} disabled={!loadedHere || !paused}><Play /> Resume</Button>
						<Button variant="secondary" onclick={() => run('job.step')} disabled={!loadedHere || running}><StepForward /> Step</Button>
						<Button variant="secondary" onclick={() => run('job.skip')} disabled={!paused} title="Leave out the placement it stopped at"><SkipForward /> Skip</Button>
						<Button variant="outline" class="text-alarm" onclick={() => run('job.stop')} disabled={!loadedHere || (!running && !paused)}><Square /> Stop</Button>
						<div class="ml-auto flex items-center gap-2">
							<Switch id="dry" bind:checked={dryRun} />
							<Label for="dry" class="flex items-center gap-1 text-xs font-normal"><FlaskConical class="size-3.5" /> Dry run (no picking)</Label>
						</div>
					</div>
					{#if loadedHere}
						<div class="flex items-center gap-3">
							<div class="h-2 flex-1 overflow-hidden rounded-full bg-muted">
								<div class="h-full rounded-full bg-primary transition-all" style="width: {progress.total ? (100 * progress.done) / progress.total : 0}%"></div>
							</div>
							<span class="num text-sm">{progress.done} / {progress.total}</span>
							<span class="num text-xs text-muted-foreground">{fmt(job.elapsed, 0)} s</span>
						</div>
					{/if}
					{#if plan.length}
						<div class="max-h-72 overflow-y-auto rounded-lg border">
							<Table.Root>
								<Table.Header>
									<Table.Row>
										<Table.Head class="w-8"></Table.Head>
										<Table.Head>Ref</Table.Head>
										<Table.Head>Part</Table.Head>
										<Table.Head>Board</Table.Head>
										<Table.Head class="text-right">X</Table.Head>
										<Table.Head class="text-right">Y</Table.Head>
										<Table.Head class="text-right">R</Table.Head>
									</Table.Row>
								</Table.Header>
								<Table.Body>
									{#each plan as p (p.board + p.placement)}
										<Table.Row class={job.current_placement === p.placement && !p.done ? 'bg-primary/10' : p.done ? 'opacity-50' : ''}>
											<Table.Cell>{#if p.done}<Check class="size-4 text-ok" />{/if}</Table.Cell>
											<Table.Cell class="font-mono">{p.placement}</Table.Cell>
											<Table.Cell class="font-mono text-xs">{p.part ?? '–'}</Table.Cell>
											<Table.Cell class="text-xs">{p.board}</Table.Cell>
											<Table.Cell class="num text-right">{fmt(p.location.x, 2)}</Table.Cell>
											<Table.Cell class="num text-right">{fmt(p.location.y, 2)}</Table.Cell>
											<Table.Cell class="num text-right">{fmt(p.location.rotation, 1)}</Table.Cell>
										</Table.Row>
									{/each}
								</Table.Body>
							</Table.Root>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>

			<!-- boards in the job -->
			<Card.Root class="gap-3">
				<Card.Header class="flex items-center gap-2">
					<Card.Title class="text-sm">Boards in this job</Card.Title>
					<Card.Action class="flex items-center gap-1.5">
						<select class="h-8 rounded-md border border-input bg-background px-2 text-xs" onchange={(e) => { addBoard((e.target as HTMLSelectElement).value); (e.target as HTMLSelectElement).value = ''; }}>
							<option value="">Add board…</option>
							{#each boards as b (b.id)}<option value={b.id}>{b.name ?? b.id} ({b.placements.length})</option>{/each}
						</select>
						<Button size="sm" variant="ghost" onclick={() => run('job.reset', { job_id: draft!.id })} title="Mark every placement as not placed"><RotateCcw /> Reset placed</Button>
						<Button size="sm" disabled={!dirty} onclick={save}><Save /> Save</Button>
					</Card.Action>
				</Card.Header>
				<Card.Content>
					<!-- as OpenPnP's job settings -->
					<div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
						<label class="flex items-center gap-1.5">Order
							<select class="h-8 rounded-md border border-input bg-background px-2 text-xs" value={draft.order ?? 'nozzle_tips'} onchange={(e) => { draft!.order = e.currentTarget.value; dirty = true; }}>
								<option value="nozzle_tips">By nozzle tip (OpenPnP's default)</option>
								<option value="part">By part</option>
								<option value="part_height">By part height, low first</option>
								<option value="unsorted">As in the board</option>
							</select>
						</label>
						<label class="flex items-center gap-1.5">On an error
							<select class="h-8 rounded-md border border-input bg-background px-2 text-xs" value={draft.error_handling ?? 'alert'} onchange={(e) => { draft!.error_handling = e.currentTarget.value; dirty = true; }}>
								<option value="alert">Pause (Alert)</option>
								<option value="defer">Retry later, then skip (Defer)</option>
							</select>
						</label>
						{#if (draft.error_handling ?? 'alert') === 'defer'}
							<label class="flex items-center gap-1.5">Retries
								<Input type="number" min="0" max="50" class="h-8 w-16 font-mono text-xs" value={draft.max_placement_retries ?? 5} oninput={(e) => { draft!.max_placement_retries = Number(e.currentTarget.value); dirty = true; }} />
							</label>
						{/if}
					</div>
					{#if draft.boards.length}
						<Table.Root>
							<Table.Header>
								<Table.Row>
									<Table.Head>Board</Table.Head>
									<Table.Head>X</Table.Head>
									<Table.Head>Y</Table.Head>
									<Table.Head>Z (surface)</Table.Head>
									<Table.Head>Rotation</Table.Head>
									<Table.Head>Side</Table.Head>
									<Table.Head>On</Table.Head>
									<Table.Head title="Measure the board's fiducials before placing">Fiducials</Table.Head>
									<Table.Head>Placed</Table.Head>
									<Table.Head></Table.Head>
								</Table.Row>
							</Table.Header>
							<Table.Body>
								{#each draft.boards as b, i (i)}
									<Table.Row>
										<Table.Cell class="font-medium">{b.board}</Table.Cell>
										{#each ['x', 'y', 'z', 'rotation'] as const as k (k)}
											<Table.Cell>
												<Input type="number" step="0.01" class="h-8 w-24 font-mono text-xs" bind:value={b.location[k]} oninput={() => (dirty = true)} />
											</Table.Cell>
										{/each}
										<Table.Cell>
											<ToggleGroup.Root type="single" variant="outline" size="sm" value={b.side} onValueChange={(v) => { if (v) { b.side = v; dirty = true; } }}>
												<ToggleGroup.Item value="top" class={segmentItem}>Top</ToggleGroup.Item>
												<ToggleGroup.Item value="bottom" class={segmentItem}>Bottom</ToggleGroup.Item>
											</ToggleGroup.Root>
										</Table.Cell>
										<Table.Cell><Switch bind:checked={b.enabled} onCheckedChange={() => (dirty = true)} /></Table.Cell>
										<Table.Cell><Switch bind:checked={b.check_fiducials} onCheckedChange={() => (dirty = true)} /></Table.Cell>
										<Table.Cell class="num text-xs">{b.placed.length}</Table.Cell>
										<Table.Cell class="text-right">
											<Button variant="ghost" size="icon-sm" title="Board origin = where the top camera is now" onclick={() => fromCamera(i)}><Crosshair /></Button>
											<Button variant="ghost" size="icon-sm" title="Measure the fiducials now and correct the board's location" disabled={checking !== null} onclick={() => checkFiducials(i)}>
												{#if checking === i}<LoaderCircle class="animate-spin" />{:else}<ScanSearch />{/if}
											</Button>
											<Button variant="ghost" size="icon-sm" title="Remove from the job" onclick={() => { draft!.boards.splice(i, 1); dirty = true; }}><Trash2 /></Button>
										</Table.Cell>
									</Table.Row>
								{/each}
							</Table.Body>
						</Table.Root>
						<p class="mt-2 text-xs text-muted-foreground">
							X/Y: where the board's origin is on the machine (put the top camera's crosshair on it and press <Crosshair class="inline size-3" />);
							Z: the board's top surface; rotation: the board's angle on the machine. With Fiducials on, the job measures the
							board's fiducials (2 or more, round) with the top camera before its first placement and corrects X, Y and rotation;
							<ScanSearch class="inline size-3" /> does that now and saves the result.
						</p>
					{:else}
						<p class="text-sm text-muted-foreground">Add a board (import one from KiCad in the Library).</p>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>
	{:else}
		<Card.Root><Card.Content class="py-10 text-center text-sm text-muted-foreground">Create a job on the left.</Card.Content></Card.Root>
	{/if}
</div>
