<script lang="ts">
	import './layout.css';
	import favicon from '#lib/assets/favicon.svg';
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import type { LayoutProps } from './$types';
	import { tinker } from '#lib/api/client.svelte.js';
	import { Badge } from '#lib/components/ui/badge/index.js';
	import { Cpu, Boxes, Camera, Settings2, SquareTerminal, Wifi, WifiOff, LoaderCircle } from '@lucide/svelte';

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
</script>

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
			<Badge variant="outline">machine: {machineState}</Badge>
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
