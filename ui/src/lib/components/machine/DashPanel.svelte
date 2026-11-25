<script lang="ts">
	// One dashboard panel: a shadcn Card with a drag handle, icon, title, actions and a collapse button.
	import type { Component, Snippet } from 'svelte';
	import * as Card from '#lib/components/ui/card/index.js';
	import { Button } from '#lib/components/ui/button/index.js';
	import { ChevronDown, GripVertical } from '@lucide/svelte';
	import type { Dashboard } from '#lib/dashboard.svelte.js';

	let {
		id,
		title,
		icon: Icon,
		dash,
		actions,
		children,
		bodyClass = ''
	}: {
		id: string;
		title: string;
		icon: Component;
		dash: Dashboard;
		actions?: Snippet;
		children: Snippet;
		bodyClass?: string;
	} = $props();

	const collapsed = $derived(dash.isCollapsed(id));
</script>

<Card.Root class="gap-0 py-0 transition-opacity {dash.dragging === id ? 'opacity-40' : ''}" data-panel={id}>
	<!-- py-0 / [.border-b]:pb-0: the card header pads its bottom when it has a border, which pushed the content up -->
	<Card.Header class="flex h-11 items-center gap-2 px-3 py-0 [.border-b]:pb-0 {collapsed ? '' : 'border-b'}">
		<span
			role="button"
			tabindex="-1"
			aria-label="drag to move the {title} panel"
			title="Drag to move"
			draggable="true"
			class="-ml-1 cursor-grab text-muted-foreground/50 hover:text-muted-foreground active:cursor-grabbing"
			ondragstart={(e) => {
				e.dataTransfer?.setData('text/plain', id);
				if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
				dash.dragging = id;
			}}
			ondragend={() => (dash.dragging = null)}
		>
			<GripVertical class="size-4" />
		</span>
		<Icon class="size-4 text-muted-foreground" />
		<Card.Title class="text-sm">{title}</Card.Title>
		<Card.Action class="flex items-center gap-1 self-center">
			{#if actions && !collapsed}{@render actions()}{/if}
			<Button variant="ghost" size="icon-sm" onclick={() => dash.toggle(id)} title={collapsed ? 'Expand' : 'Collapse'}>
				<ChevronDown class="transition-transform {collapsed ? '-rotate-90' : ''}" />
			</Button>
		</Card.Action>
	</Card.Header>
	{#if !collapsed}
		<Card.Content class="py-4 {bodyClass}">
			{@render children()}
		</Card.Content>
	{/if}
</Card.Root>
