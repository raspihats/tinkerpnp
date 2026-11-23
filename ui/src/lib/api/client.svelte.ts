// TinkerPnP API client: JSON-RPC 2.0 over WebSocket (docs/api.md), with automatic reconnect and a reactive
// status store fed by machine.objects.subscribe. Every component reads machine state from `tinker.status`.

export type Status = Record<string, Record<string, unknown>>;

export interface ServerInfo {
	name: string;
	version: string;
	api_version: string;
	hostname: string;
	machine_name: string;
	plugins: { name: string; version: string }[];
	state: string;
	warnings: string[];
}

export class RpcError extends Error {
	constructor(
		public code: number,
		message: string,
		public data: { name?: string; [k: string]: unknown } = {}
	) {
		super(message);
	}
}

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: number };

class TinkerClient {
	connection = $state<'connecting' | 'connected' | 'disconnected'>('disconnected');
	info = $state<ServerInfo | null>(null);
	objects = $state<string[]>([]);
	status = $state<Status>({});
	lastError = $state<string | null>(null);
	/** bumped on every notify_config_changed; pages that show configuration re-load when it changes */
	configRevision = $state(0);
	/** the last failed action (shown under the header until dismissed or replaced) */
	notice = $state<{ text: string; name?: string; time: number } | null>(null);
	/** actions in flight, by method, for busy indicators */
	busy = $state<Record<string, number>>({});

	#ws: WebSocket | null = null;
	#nextId = 1;
	#pending = new Map<number, Pending>();
	#retry = 0;
	#started = false;
	#listeners = new Map<string, Set<(params: Record<string, unknown>) => void>>();

	/** Listen to a server notification (e.g. notify_gcode). Returns a function that stops listening. */
	on(method: string, fn: (params: Record<string, unknown>) => void): () => void {
		let set = this.#listeners.get(method);
		if (!set) this.#listeners.set(method, (set = new Set()));
		set.add(fn);
		return () => set.delete(fn);
	}

	start(): void {
		if (this.#started) return;
		this.#started = true;
		this.#connect();
	}

	#url(): string {
		const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
		return `${proto}//${location.host}/websocket`;
	}

	#connect(): void {
		this.connection = 'connecting';
		const ws = new WebSocket(this.#url());
		this.#ws = ws;
		ws.onopen = () => {
			this.#retry = 0;
			this.connection = 'connected';
			this.lastError = null;
			this.#afterConnect().catch((e) => (this.lastError = String(e)));
		};
		ws.onmessage = (ev) => this.#onMessage(ev.data);
		ws.onclose = () => {
			this.connection = 'disconnected';
			for (const p of this.#pending.values()) {
				clearTimeout(p.timer);
				p.reject(new Error('connection closed'));
			}
			this.#pending.clear();
			const delay = Math.min(10000, 500 * 2 ** this.#retry++);
			setTimeout(() => this.#connect(), delay);
		};
	}

	async #afterConnect(): Promise<void> {
		await this.call('server.connection.identify', {
			client_name: 'TinkerUI',
			version: '0.0.1',
			type: 'web'
		});
		this.info = (await this.call('server.info')) as ServerInfo;
		const list = (await this.call('machine.objects.list')) as { objects: string[] };
		this.objects = list.objects;
		// Phase 0: subscribe to everything; later pages subscribe to what they show.
		const req: Record<string, null> = {};
		for (const name of list.objects) req[name] = null;
		const sub = (await this.call('machine.objects.subscribe', { objects: req, rate: 10 })) as {
			status: Status;
		};
		this.status = sub.status;
	}

	#onMessage(text: string): void {
		const msg = JSON.parse(text);
		if (Array.isArray(msg)) {
			msg.forEach((m) => this.#handle(m));
		} else {
			this.#handle(msg);
		}
	}

	#handle(msg: {
		id?: number;
		result?: unknown;
		error?: { code: number; message: string; data?: object };
		method?: string;
		params?: { status?: Status; revision?: number; [k: string]: unknown };
	}): void {
		if (msg.id !== undefined && msg.id !== null) {
			const p = this.#pending.get(msg.id);
			if (!p) return;
			this.#pending.delete(msg.id);
			clearTimeout(p.timer);
			if (msg.error) p.reject(new RpcError(msg.error.code, msg.error.message, msg.error.data as RpcError["data"]));
			else p.resolve(msg.result);
			return;
		}
		if (msg.method === 'notify_status_update' && msg.params?.status) {
			this.#merge(msg.params.status);
		} else if (msg.method === 'notify_config_changed') {
			// the machine was rebuilt from the new configuration: objects may have changed, subscribe again
			this.configRevision = msg.params?.revision ?? this.configRevision + 1;
			this.#afterConnect().catch((e) => (this.lastError = String(e)));
		}
		if (msg.method) {
			for (const fn of this.#listeners.get(msg.method) ?? []) fn(msg.params ?? {});
		}
	}

	#merge(delta: Status): void {
		for (const [name, fields] of Object.entries(delta)) {
			const current = (this.status[name] ??= {});
			for (const [key, value] of Object.entries(fields)) {
				const old = current[key];
				// objects inside fields (e.g. location) are merged one level deep, as the API specifies
				if (value && typeof value === 'object' && !Array.isArray(value) && old && typeof old === 'object' && !Array.isArray(old)) {
					current[key] = { ...(old as object), ...(value as object) };
				} else {
					current[key] = value;
				}
			}
		}
	}

	/** Call a method for a user action: failures become `notice` instead of exceptions. Returns null on failure. */
	async act<T = unknown>(method: string, params: Record<string, unknown> = {}, timeoutMs = 120000): Promise<T | null> {
		this.busy[method] = (this.busy[method] ?? 0) + 1;
		try {
			const r = await this.call<T>(method, params, timeoutMs);
			return r;
		} catch (e) {
			if (e instanceof RpcError) this.notice = { text: e.message, name: e.data?.name, time: Date.now() };
			else this.notice = { text: `${method}: ${e}`, time: Date.now() };
			return null;
		} finally {
			this.busy[method] = Math.max(0, (this.busy[method] ?? 1) - 1);
		}
	}

	isBusy(...methods: string[]): boolean {
		return methods.some((m) => (this.busy[m] ?? 0) > 0);
	}

	call<T = unknown>(method: string, params: Record<string, unknown> = {}, timeoutMs = 30000): Promise<T> {
		const ws = this.#ws;
		if (!ws || ws.readyState !== WebSocket.OPEN) {
			return Promise.reject(new Error('not connected'));
		}
		const id = this.#nextId++;
		return new Promise<T>((resolve, reject) => {
			const timer = window.setTimeout(() => {
				this.#pending.delete(id);
				reject(new Error(`${method}: timeout`));
			}, timeoutMs);
			this.#pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
			ws.send(JSON.stringify({ jsonrpc: '2.0', id, method, params }));
		});
	}
}

export const tinker = new TinkerClient();

// ── helpers for templates ─────────────────────────────────────────────────────────────────────────────────────────
export function kindOf(name: string): string {
	return name.includes('/') ? name.split('/')[0] : name;
}

export function idOf(name: string): string {
	return name.includes('/') ? name.split('/')[1] : name;
}

export function fmt(v: unknown, digits = 3): string {
	if (v === null || v === undefined) return '–';
	if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(digits);
	if (typeof v === 'boolean') return v ? 'yes' : 'no';
	return String(v);
}

export type Loc = { x?: number; y?: number; z?: number; rotation?: number };
