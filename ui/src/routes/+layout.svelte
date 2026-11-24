<script lang="ts">
	import './layout.css';
	import favicon from '#lib/assets/favicon.svg';
	import { page, updated } from '$app/state';
	import { onMount } from 'svelte';
	import type { LayoutProps } from './$types';
	import { tinker } from '#lib/api/client.svelte.js';
	import { stateVariant } from '#lib/status.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { Separator } from '#lib/components/ui/separator/index.js';
	import {
		Cpu,
		Boxes,
		Camera,
		Settings2,
		SquareTerminal,
		LoaderCircle,
		OctagonX,
		X,
		Sun,
		Moon,
		TriangleAlert,
		RefreshCw,
		Crosshair
	} from '@lucide/svelte';

	let { children }: LayoutProps = $props();

	onMount(() => tinker.start());

	const nav = [
		{ href: '/', label: 'Machine', icon: Cpu },
		{ href: '/cameras', label: 'Cameras', icon: Camera },
		{ href: '/console', label: 'Console', icon: SquareTerminal },
		{ href: '/objects', label: 'Objects', icon: Boxes },
		{ href: '/config', label: 'Configuration', icon: Settings2 }
	];

	const machine = $derived(tinker.status['machine'] ?? {});
	const machineState = $derived((machine.state as string | undefined) ?? 'offline');
	const enabled = $derived(machine.enabled === true);
	const current = $derived(nav.find((n) => n.href === page.url.pathname) ?? nav[0]);

	// theme: dark unless chosen otherwise (app.html sets it before the first paint)
	let dark = $state(true);
	onMount(() => (dark = document.documentElement.classList.contains('dark')));
	function toggleTheme() {
		dark = !dark;
		document.documentElement.classList.toggle('dark', dark);
		try {
			localStorage.setItem('tinkerui.theme', dark ? 'dark' : 'light');
		} catch {
			/* private window: not remembered */
		}
	}

	function stop() {
		tinker.act('machine.stop', { reason: 'stop button (TinkerUI)' });
	}

	// Esc stops the machine from any page
	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape' && enabled) {
			e.preventDefault();
			stop();
		}
	}
</script>

<svelte:window onkeydown={onKey} />

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>{current.label} · TinkerPnP{tinker.info ? ` · ${tinker.info.machine_name}` : ''}</title>
</svelte:head>

<div class="flex min-h-screen bg-background text-foreground">
	<!-- sidebar (md and up) -->
	<aside class="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
		<a href="/" class="flex items-center gap-2.5 px-4 pt-4 pb-3">
			<span class="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
				<Crosshair class="size-4.5" strokeWidth={2.5} />
			</span>
			<span class="leading-tight">
				<span class="block text-sm font-semibold tracking-tight text-foreground">TinkerPnP</span>
				<span class="block max-w-36 truncate text-[11px] text-muted-foreground">{tinker.info?.machine_name ?? 'pick and place'}</span>
			</span>
		</a>
		<Separator />
		<nav class="flex flex-1 flex-col gap-0.5 p-2">
			{#each nav as item (item.href)}
				{@const active = page.url.pathname === item.href}
				<a
					href={item.href}
					aria-current={active ? 'page' : undefined}
					class="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors {active
						? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
						: 'hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground'}"
				>
					<item.icon class="size-4 {active ? 'text-primary' : ''}" />
					{item.label}
				</a>
			{/each}
		</nav>
		<Separator />
		<div class="flex flex-col gap-2 p-3 text-xs">
			<div class="flex items-center gap-2">
				<span
					class="size-2 rounded-full {tinker.connection === 'connected'
						? 'bg-ok'
						: tinker.connection === 'connecting'
							? 'animate-pulse bg-warn'
							: 'bg-alarm'}"
				></span>
				<span class="text-muted-foreground">
					{tinker.connection === 'connected' ? `server ${tinker.info?.hostname ?? ''}` : tinker.connection}
				</span>
				<Button variant="ghost" size="icon-sm" class="ml-auto" onclick={toggleTheme} title={dark ? 'Light theme' : 'Dark theme'}>
					{#if dark}<Sun />{:else}<Moon />{/if}
				</Button>
			</div>
			{#if tinker.info}
				<span class="text-[11px] text-muted-foreground/70">TinkerPnP {tinker.info.version} · API {tinker.info.api_version}</span>
			{/if}
		</div>
	</aside>

	<div class="flex min-w-0 flex-1 flex-col">
		<!-- top bar -->
		<header class="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur">
			<nav class="flex gap-0.5 md:hidden">
				{#each nav as item (item.href)}
					<Button href={item.href} variant={page.url.pathname === item.href ? 'secondary' : 'ghost'} size="icon-sm" title={item.label}>
						<item.icon />
					</Button>
				{/each}
			</nav>
			<h1 class="hidden text-base font-semibold tracking-tight sm:block">{current.label}</h1>
			<div class="flex items-center gap-1.5">
				<Badge variant={stateVariant(machineState)} class="capitalize">
					{#if ['homing', 'enabling', 'moving'].includes(machineState)}<LoaderCircle class="animate-spin" />{/if}
					{machineState}
				</Badge>
				{#if enabled}
					<Badge variant={machine.homed ? 'secondary' : 'warn'}>{machine.homed ? 'homed' : 'not homed'}</Badge>
				{/if}
			</div>
			{#if machine.message}<span class="hidden truncate text-xs text-muted-foreground lg:block">{machine.message}</span>{/if}
			<div class="ml-auto">
				<Button
					variant="destructive"
					class="h-9 gap-2 px-4 font-semibold tracking-wide {enabled ? 'bg-alarm text-white hover:bg-alarm/90 dark:bg-alarm' : ''}"
					disabled={!enabled}
					onclick={stop}
					title="Stop all motion now (Esc)"
				>
					<OctagonX class="size-5" /> STOP
					<kbd class="hidden rounded bg-black/20 px-1.5 py-0.5 font-mono text-[10px] font-normal sm:inline">Esc</kbd>
				</Button>
			</div>
		</header>

		{#if tinker.info?.warnings?.length}
			<div class="flex items-start gap-2 border-b bg-warn/10 px-4 py-2 text-sm">
				<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warn" />
				<div>{#each tinker.info.warnings as w (w)}<div>{w}</div>{/each}</div>
			</div>
		{/if}
		{#if updated.current}
			<div class="flex items-center gap-2 border-b bg-primary/10 px-4 py-1.5 text-sm">
				<RefreshCw class="size-4 text-primary" /> A new version of the web UI is on the server.
				<Button size="sm" variant="outline" class="ml-auto" onclick={() => location.reload()}>Reload</Button>
			</div>
		{/if}
		{#if tinker.notice}
			<div class="flex items-center gap-2 border-b bg-alarm/10 px-4 py-1.5 text-sm">
				<TriangleAlert class="size-4 shrink-0 text-alarm" />
				<span>{tinker.notice.text}</span>
				{#if tinker.notice.name}<Badge variant="outline" class="font-mono">{tinker.notice.name}</Badge>{/if}
				<Button variant="ghost" size="icon-sm" class="ml-auto" title="Dismiss" onclick={() => (tinker.notice = null)}><X /></Button>
			</div>
		{/if}

		<main class="min-w-0 flex-1 p-4 lg:p-5">
			{#if tinker.connection !== 'connected' && !tinker.objects.length}
				<div class="flex h-64 flex-col items-center justify-center gap-3 text-muted-foreground">
					<LoaderCircle class="size-6 animate-spin" />
					<span class="text-sm">Connecting to the TinkerPnP server…</span>
				</div>
			{:else}
				{@render children()}
			{/if}
		</main>
	</div>
</div>
