// Reading the JSON Schemas of object types (config.types) to build forms. Pydantic writes nullable fields as
// anyOf [X, {type: null}], nested models as $ref into $defs, and tuples as prefixItems.

export type Schema = {
	type?: string | string[];
	title?: string;
	description?: string;
	default?: unknown;
	enum?: unknown[];
	const?: unknown;
	properties?: Record<string, Schema>;
	required?: string[];
	additionalProperties?: boolean | Schema;
	items?: Schema;
	prefixItems?: Schema[];
	minItems?: number;
	maxItems?: number;
	minimum?: number;
	maximum?: number;
	anyOf?: Schema[];
	$ref?: string;
	$defs?: Record<string, Schema>;
};

export type Defs = Record<string, Schema>;

export type FieldKind = 'string' | 'number' | 'integer' | 'boolean' | 'enum' | 'object' | 'range' | 'strings' | 'json';

export function deref(s: Schema, defs: Defs): Schema {
	let out = s;
	for (let i = 0; i < 10 && out.$ref; i++) out = defs[out.$ref.split('/').pop() ?? ''] ?? {};
	return out;
}

/** The schema without the "or null" of an optional field, and whether null was allowed. */
export function unwrap(s: Schema, defs: Defs): { schema: Schema; nullable: boolean } {
	if (s.anyOf) {
		const rest = s.anyOf.filter((a) => a.type !== 'null');
		const nullable = rest.length < s.anyOf.length;
		if (rest.length === 1) return { schema: { ...deref(rest[0], defs), description: s.description ?? rest[0].description, default: s.default }, nullable };
		// e.g. number | string (a value with units): treat as the first simple type
		const simple = rest.find((a) => typeof a.type === 'string' && a.type !== 'object' && a.type !== 'array');
		if (simple) return { schema: { ...simple, description: s.description, default: s.default }, nullable };
		return { schema: { description: s.description, default: s.default }, nullable };
	}
	return { schema: deref(s, defs), nullable: false };
}

export function kindOf(s: Schema): FieldKind {
	if (s.enum) return 'enum';
	if (s.type === 'boolean') return 'boolean';
	if (s.type === 'integer') return 'integer';
	if (s.type === 'number') return 'number';
	if (s.type === 'string') return 'string';
	if (s.type === 'array') {
		const p = s.prefixItems;
		if (p?.length === 2 && p.every((x) => x.type === 'number' || x.type === 'integer')) return 'range';
		if (s.items?.type === 'string') return 'strings';
		return 'json';
	}
	if (s.properties && Object.keys(s.properties).length) return 'object';
	return 'json';
}

/** A typed value from a text input: numbers become numbers, anything else (e.g. "6000 mm/min") stays text. */
export function parseScalar(text: string, kind: FieldKind): unknown {
	const t = text.trim();
	if ((kind === 'number' || kind === 'integer') && /^-?(\d+\.?\d*|\.\d+)(e-?\d+)?$/i.test(t)) return Number(t);
	return t;
}

export function label(name: string): string {
	return name.replaceAll('_', ' ');
}

/** Which existing ids a field refers to, by its name (driver, input, tip, ...) or its parent (axes.x, ...). */
export function refKind(field: string, parent: string | null): string | null {
	const byName: Record<string, string> = {
		driver: 'drivers',
		input: 'axes',
		tip: 'nozzle_tips',
		vacuum: 'actuators',
		head: 'heads',
		camera: 'cameras',
		pump: 'actuators'
	};
	if (byName[field]) return byName[field];
	if (parent === 'axes') return 'axes';
	return null;
}

/** The field schema at a dotted path ("format.width"), without its "or null"; {} when the schema doesn't have it. */
export function schemaAt(root: Schema | undefined, defs: Defs, path: string): Schema {
	let s: Schema | undefined = root ? deref(root, defs) : undefined;
	let out: Schema = {};
	for (const part of path.split('.')) {
		const field = s?.properties?.[part];
		if (!field) return {};
		out = unwrap(field, defs).schema;
		s = out;
	}
	return out;
}
