// Dashboard layouts (Machine page): panels in columns, rearranged by dragging, collapsible. Like Mainsail/Fluidd,
// there is one layout per column count (the screen width picks 1, 2 or 3 columns), each remembered in this browser.

export type Columns = string[][];

type Stored = { layouts: Record<string, Columns>; collapsed: string[] };

export class Dashboard {
	layouts = $state<Record<string, Columns>>({});
	collapsed = $state<string[]>([]);
	width = $state(1400);
	dragging = $state<string | null>(null);

	constructor(
		private key: string,
		private defaults: Record<number, Columns>
	) {
		this.layouts = Object.fromEntries(Object.entries(defaults).map(([n, cols]) => [n, cols.map((c) => [...c])]));
		try {
			const raw = localStorage.getItem(key);
			if (raw) {
				const stored = JSON.parse(raw) as Stored;
				for (const [n, cols] of Object.entries(stored.layouts ?? {})) {
					if (this.defaults[Number(n)]) this.layouts[n] = this.complete(cols, Number(n));
				}
				this.collapsed = stored.collapsed ?? [];
			}
		} catch {
			/* private window or bad data: defaults */
		}
	}

	/** 1, 2 or 3 columns, from the width of the dashboard area */
	get count(): number {
		return this.width >= 1500 ? 3 : this.width >= 1000 ? 2 : 1;
	}

	get columns(): Columns {
		return this.layouts[this.count] ?? this.defaults[this.count];
	}

	/** keep a stored layout in step with the panels that exist now (new ones are added, gone ones dropped) */
	private complete(cols: Columns, n: number): Columns {
		const known = new Set(this.defaults[n].flat());
		const out = Array.from({ length: n }, (_, i) => (cols[i] ?? []).filter((id) => known.has(id)));
		const placed = new Set(out.flat());
		this.defaults[n].forEach((col, i) => col.forEach((id) => !placed.has(id) && out[i].push(id)));
		return out;
	}

	private save() {
		try {
			localStorage.setItem(this.key, JSON.stringify({ layouts: this.layouts, collapsed: this.collapsed }));
		} catch {
			/* not remembered */
		}
	}

	isCollapsed(id: string): boolean {
		return this.collapsed.includes(id);
	}

	toggle(id: string) {
		this.collapsed = this.isCollapsed(id) ? this.collapsed.filter((x) => x !== id) : [...this.collapsed, id];
		this.save();
	}

	/** move a panel to column `col`, before panel `before` (or to the end) */
	move(id: string, col: number, before: string | null) {
		const cols = this.columns.map((c) => c.filter((x) => x !== id));
		const target = cols[col];
		const at = before ? target.indexOf(before) : -1;
		if (at >= 0) target.splice(at, 0, id);
		else target.push(id);
		this.layouts[this.count] = cols;
		this.save();
	}

	reset() {
		this.layouts[this.count] = this.defaults[this.count].map((c) => [...c]);
		this.collapsed = [];
		this.save();
	}
}
