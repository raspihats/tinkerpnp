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
	import * as Popover from '#lib/components/ui/popover/index.js';
	import * as Tooltip from '#lib/components/ui/tooltip/index.js';
	import { Toaster } from '#lib/components/ui/sonner/index.js';
	import { ModeWatcher, mode, toggleMode } from 'mode-watcher';
	import {
		Cpu,
		ListChecks,
		Library,
		Boxes,
		Camera,
		Settings2,
		SquareTerminal,
		LoaderCircle,
		OctagonX,
		Sun,
		Moon,
		TriangleAlert,
		RefreshCw,
		Crosshair,
		Bell,
		CircleCheck,
		Info,
		CircleX,
		PanelLeftClose,
		PanelLeftOpen
	} from '@lucide/svelte';

	let { children }: LayoutProps = $props();

	onMount(() => tinker.start());

	const nav = [
		{ href: '/', label: 'Machine', icon: Cpu },
		{ href: '/jobs', label: 'Jobs', icon: ListChecks },
		{ href: '/library', label: 'Library', icon: Library },
		{ href: '/cameras', label: 'Cameras', icon: Camera },
		{ href: '/console', label: 'Console', icon: SquareTerminal },
		{ href: '/objects', label: 'Objects', icon: Boxes },
		{ href: '/config', label: 'Configuration', icon: Settings2 }
	];

	const machine = $derived(tinker.status['machine'] ?? {});
	const machineState = $derived((machine.state as string | undefined) ?? 'offline');
	const enabled = $derived(machine.enabled === true);
	const current = $derived(nav.find((n) => n.href === page.url.pathname) ?? nav[0]);

	// theme: mode-watcher (shadcn's), dark unless the viewer chose light; remembered per browser
	const dark = $derived(mode.current !== 'light');
	const toggleTheme = () => toggleMode();

	const levelIcon = { error: CircleX, warn: TriangleAlert, info: Info, ok: CircleCheck } as const;
	const levelColor = { error: 'text-alarm', warn: 'text-warn', info: 'text-muted-foreground', ok: 'text-ok' } as const;
	const time = (t: number) => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

	function stop() {
		tinker.act('machine.stop', { reason: 'stop button (TinkerUI)' });
	}

	// sidebar: full or icons only (Ctrl+B or the button in the top bar); remembered per browser
	const NAV_KEY = 'tinkerpnp.nav.collapsed';
	let navCollapsed = $state(false);
	onMount(() => {
		try {
			navCollapsed = localStorage.getItem(NAV_KEY) === '1';
		} catch {
			/* no storage */
		}
	});
	function toggleNav() {
		navCollapsed = !navCollapsed;
		try {
			localStorage.setItem(NAV_KEY, navCollapsed ? '1' : '0');
		} catch {
			/* not remembered */
		}
	}

	// Esc stops the machine from any page; Ctrl+B folds the sidebar
	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape' && enabled) {
			e.preventDefault();
			stop();
		} else if (e.key.toLowerCase() === 'b' && (e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey) {
			e.preventDefault();
			toggleNav();
		}
	}
</script>

<svelte:window onkeydown={onKey} />
<ModeWatcher defaultMode="dark" track={false} />
<Toaster position="bottom-right" richColors closeButton />

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>{current.label} · TinkerPnP{tinker.info ? ` · ${tinker.info.machine_name}` : ''}</title>
</svelte:head>

<div class="flex min-h-screen bg-background text-foreground">
	<!-- sidebar (md and up): full width or icons only. The icons keep their place (centred in the 3.5rem rail) while
	     the width animates, and the labels fade out. -->
	<Tooltip.Provider delayDuration={200}>
	<aside
		class="sticky top-0 hidden h-screen shrink-0 flex-col overflow-hidden border-r bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out md:flex {navCollapsed
			? 'w-14'
			: 'w-56'}"
	>
		<a href="/" class="flex items-center gap-2.5 px-3 pt-4 pb-3" title={navCollapsed ? 'TinkerPnP' : undefined}>
			<span class="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
				<Crosshair class="size-4.5" strokeWidth={2.5} />
			</span>
			<span class="leading-tight whitespace-nowrap transition-opacity duration-150 {navCollapsed ? 'opacity-0' : 'opacity-100'}">
				<span class="block text-sm font-semibold tracking-tight text-foreground">TinkerPnP</span>
				<span class="block max-w-36 truncate text-[11px] text-muted-foreground">{tinker.info?.machine_name ?? 'pick and place'}</span>
			</span>
		</a>
		<Separator />
		<nav class="flex flex-1 flex-col gap-0.5 p-2">
			{#each nav as item (item.href)}
				{@const active = page.url.pathname === item.href}
				<Tooltip.Root disabled={!navCollapsed}>
					<Tooltip.Trigger>
						{#snippet child({ props })}
							<a
								{...props}
								href={item.href}
								aria-current={active ? 'page' : undefined}
								aria-label={navCollapsed ? item.label : undefined}
								class="flex h-9 items-center gap-2.5 rounded-md px-3 text-sm whitespace-nowrap transition-colors {active
									? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
									: 'hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground'}"
							>
								<item.icon class="size-4 shrink-0 {active ? 'text-primary' : ''}" />
								<span class="transition-opacity duration-150 {navCollapsed ? 'opacity-0' : 'opacity-100'}">{item.label}</span>
							</a>
						{/snippet}
					</Tooltip.Trigger>
					<Tooltip.Content side="right" sideOffset={8}>{item.label}</Tooltip.Content>
				</Tooltip.Root>
			{/each}
		</nav>
		<Separator />
		<div class="flex flex-col gap-2 p-3 text-xs">
			<div class="flex items-center gap-2 {navCollapsed ? 'flex-col' : ''}">
				<span
					title={navCollapsed ? (tinker.connection === 'connected' ? `server ${tinker.info?.hostname ?? ''}` : tinker.connection) : undefined}
					class="size-2 shrink-0 rounded-full {tinker.connection === 'connected'
						? 'bg-ok'
						: tinker.connection === 'connecting'
							? 'animate-pulse bg-warn'
							: 'bg-alarm'}"
				></span>
				{#if !navCollapsed}
					<span class="truncate whitespace-nowrap text-muted-foreground">
						{tinker.connection === 'connected' ? `server ${tinker.info?.hostname ?? ''}` : tinker.connection}
					</span>
				{/if}
				<Button variant="ghost" size="icon-sm" class={navCollapsed ? '' : 'ml-auto'} onclick={toggleTheme} title={dark ? 'Light theme' : 'Dark theme'}>
					{#if dark}<Sun />{:else}<Moon />{/if}
				</Button>
			</div>
			{#if tinker.info && !navCollapsed}
				<span class="text-[11px] whitespace-nowrap text-muted-foreground/70">TinkerPnP {tinker.info.version} · API {tinker.info.api_version}</span>
			{/if}
		</div>
	</aside>
	</Tooltip.Provider>

	<div class="flex min-w-0 flex-1 flex-col">
		<!-- top bar -->
		<header class="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur">
			<Button
				variant="ghost"
				size="icon-sm"
				class="-ml-1 hidden text-muted-foreground md:inline-flex"
				onclick={toggleNav}
				title={navCollapsed ? 'Show the menu (Ctrl+B)' : 'Icons only (Ctrl+B)'}
				aria-label={navCollapsed ? 'Show the menu' : 'Collapse the menu to icons'}
			>
				{#if navCollapsed}<PanelLeftOpen />{:else}<PanelLeftClose />{/if}
			</Button>
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
			<div class="ml-auto flex items-center gap-2">
				<Popover.Root onOpenChange={(open) => open && (tinker.unread = 0)}>
					<Popover.Trigger>
						{#snippet child({ props })}
							<Button {...props} variant="ghost" size="icon" class="relative" title="Messages">
								<Bell />
								{#if tinker.unread}
									<span class="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-alarm px-1 text-[10px] font-semibold text-white">
										{tinker.unread > 9 ? '9+' : tinker.unread}
									</span>
								{/if}
							</Button>
						{/snippet}
					</Popover.Trigger>
					<Popover.Content align="end" class="w-96 p-0">
						<div class="flex items-center border-b px-3 py-2">
							<span class="text-sm font-semibold">Messages</span>
							{#if tinker.notifications.length}
								<Button variant="ghost" size="xs" class="ml-auto" onclick={() => (tinker.notifications = [])}>Clear</Button>
							{/if}
						</div>
						<div class="max-h-96 overflow-y-auto">
							{#each tinker.notifications as n (n.id)}
								{@const Icon = levelIcon[n.level]}
								<div class="flex gap-2.5 border-b px-3 py-2 text-xs last:border-0">
									<Icon class="mt-0.5 size-3.5 shrink-0 {levelColor[n.level]}" />
									<div class="min-w-0 flex-1">
										<p class="break-words">{n.text}</p>
										<p class="mt-0.5 text-[11px] text-muted-foreground">{time(n.time)}{n.name ? ` · ${n.name}` : ''}</p>
									</div>
								</div>
							{:else}
								<p class="px-3 py-6 text-center text-xs text-muted-foreground">No messages.</p>
							{/each}
						</div>
					</Popover.Content>
				</Popover.Root>
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
