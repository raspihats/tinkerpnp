// Machine state -> badge variant (colours in layout.css: ok, busy, warn, alarm)
import type { BadgeVariant } from '#lib/components/ui/badge/index.js';

export function stateVariant(state: unknown): BadgeVariant {
	switch (state) {
		case 'alarm':
		case 'error':
			return 'alarm';
		case 'homing':
		case 'enabling':
			return 'warn';
		case 'moving':
		case 'busy':
			return 'busy';
		case 'idle':
		case 'ready':
			return 'ok';
		default:
			return 'outline';
	}
}
