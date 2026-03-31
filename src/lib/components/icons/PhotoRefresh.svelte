<script lang="ts">
	export let className = 'size-4';
	export let strokeWidth = '1.5';

	// Unique ID per instance so multiple icons on the same page don't share a mask
	const maskId = `pr-${Math.random().toString(36).slice(2, 9)}`;
</script>

<svg
	xmlns="http://www.w3.org/2000/svg"
	fill="none"
	viewBox="0 0 24 24"
	stroke-width={strokeWidth}
	stroke="currentColor"
	class={className}
	aria-hidden="true"
>
	<defs>
		<!--
			The mask punches a circular hole in the photo frame paths at the
			bottom-right corner, giving the refresh badge a clean "cut-out"
			appearance on any background colour.
		-->
		<mask id={maskId}>
			<rect x="0" y="0" width="24" height="24" fill="white" />
			<circle cx="19" cy="19" r="5" fill="black" />
		</mask>
	</defs>

	<!-- Photo frame (masked so the badge corner is clear) -->
	<g mask="url(#{maskId})">
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M21 7.6V20.4C21 20.7314 20.7314 21 20.4 21H7.6C7.26863 21 7 20.7314 7 20.4V7.6C7 7.26863 7.26863 7 7.6 7H20.4C20.7314 7 21 7.26863 21 7.6Z"
		/>
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M18 4H4.6C4.26863 4 4 4.26863 4 4.6V18"
		/>
		<path stroke-linecap="round" stroke-linejoin="round" d="M7 16.8L12.4444 15L21 18" />
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M16.5 13C15.6716 13 15 12.3284 15 11.5C15 10.6716 15.6716 10 16.5 10C17.3284 10 18 10.6716 18 11.5C18 12.3284 17.3284 13 16.5 13Z"
		/>
	</g>

	<!-- Refresh badge: Refresh icon paths scaled ~34% and centred at (19, 19) -->
	<g transform="translate(19,19) scale(0.34) translate(-12,-12)" stroke-width="4.4">
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M21.8883 13.5C21.1645 18.3113 17.013 22 12 22C6.47715 22 2 17.5228 2 12C2 6.47715 6.47715 2 12 2C16.1006 2 19.6248 4.46819 21.1679 8"
		/>
		<path
			stroke-linecap="round"
			stroke-linejoin="round"
			d="M17 8H21.4C21.7314 8 22 7.73137 22 7.4V3"
		/>
	</g>
</svg>
