<script lang="ts">
	import './layout.css';
	import favicon from '#lib/assets/favicon.svg';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import type { LayoutProps } from './$types';
	import { tinker } from '#lib/api/client.svelte.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Cpu, Boxes, Camera, Settings2, SquareTerminal, Wifi, WifiOff, LoaderCircle, OctagonX, X } from '@lucide/svelte';
	import { Button } from '#lib/components/ui/button/index.js';

	let { children }: LayoutProps = $props();

	onMount(() => tinker.start());

	const nav = [
		{ href: '/', label: 'Machine', icon: Cpu },
		{ href: '/objects', label: 'Objects', icon: Boxes },
		{ href: '/cameras', label: 'Cameras', icon: Camera },
		{ href: '/console', label: 'Console', icon: SquareTerminal },
		{ href: '/config', label: 'Configuration', icon: Settings2 }
	];

	const machineState = $derived((tinker.status['machine']?.state as string | undefined) ?? '–');
	const enabled = $derived(tinker.status['machine']?.enabled === true);

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
	<title>TinkerPnP{tinker.info ? ` · ${tinker.info.machine_name}` : ''}</title>
</svelte:head>

<div class="flex min-h-screen flex-col bg-background text-foreground">
	<header class="flex items-center gap-4 border-b px-4 py-2">
		<a href="/" class="font-semibold tracking-tight">TinkerPnP</a>
		<span class="text-sm text-muted-foreground">{tinker.info?.machine_name ?? ''}</span>
		<nav class="ml-4 flex gap-1">
			{#each nav as item (item.href)}
				{@const active = page.url.pathname === item.href}
				<a
					href={item.href}
					class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm hover:bg-muted {active
						? 'bg-muted font-medium'
						: 'text-muted-foreground'}"
				>
					<item.icon class="size-4" />
					{item.label}
				</a>
			{/each}
		</nav>
		<div class="ml-auto flex items-center gap-2">
			{#if enabled}
				<Button variant="destructive" size="sm" onclick={stop} title="Stop all motion now (Esc)">
					<OctagonX class="size-4" /> STOP
				</Button>
			{/if}
			<Badge variant={machineState === 'alarm' ? 'destructive' : 'outline'}>machine: {machineState}</Badge>
			{#if tinker.connection === 'connected'}
				<Badge variant="secondary"><Wifi class="size-3" /> connected</Badge>
			{:else if tinker.connection === 'connecting'}
				<Badge variant="outline"><LoaderCircle class="size-3 animate-spin" /> connecting</Badge>
			{:else}
				<Badge variant="destructive"><WifiOff class="size-3" /> disconnected</Badge>
			{/if}
		</div>
	</header>

	{#if tinker.info?.warnings?.length}
		<div class="border-b bg-amber-50 px-4 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100">
			{#each tinker.info.warnings as w (w)}
				<div>⚠ {w}</div>
			{/each}
		</div>
	{/if}

	{#if tinker.notice}
		<div class="flex items-center gap-2 border-b bg-red-50 px-4 py-1.5 text-sm text-red-900 dark:bg-red-950 dark:text-red-100">
			<span>{tinker.notice.text}</span>
			{#if tinker.notice.name}<span class="font-mono text-xs opacity-70">{tinker.notice.name}</span>{/if}
			<button class="ml-auto" title="Dismiss" onclick={() => (tinker.notice = null)}><X class="size-4" /></button>
		</div>
	{/if}

	<main class="flex-1 bg-muted/40 p-4">
		{@render children()}
	</main>

	<footer class="border-t px-4 py-1.5 text-xs text-muted-foreground">
		{#if tinker.info}
			TinkerPnP {tinker.info.version} · API {tinker.info.api_version} · {tinker.info.hostname}
		{:else}
			not connected to the TinkerPnP server
		{/if}
	</footer>
</div>
