import { WEBUI_API_BASE_URL } from '$lib/constants';

export type ScheduledJobForm = {
	title: string;
	chat_id: string;
	model_id: string;
	prompt: string;
	schedule: string;
	hour: number;
	minute: number;
	enabled: boolean;
};

export type ScheduledJob = ScheduledJobForm & {
	id: string;
	user_id: string;
	last_run_at: number | null;
	created_at: number;
	updated_at: number;
};

export const getScheduledJobs = async (token: string): Promise<ScheduledJob[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/scheduled-jobs/`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res;
};

export const createScheduledJob = async (token: string, job: ScheduledJobForm): Promise<ScheduledJob> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/scheduled-jobs/`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(job)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res;
};

export const updateScheduledJob = async (token: string, id: string, job: ScheduledJobForm): Promise<ScheduledJob> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/scheduled-jobs/${id}`, {
		method: 'PUT',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		},
		body: JSON.stringify(job)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res;
};

export const deleteScheduledJob = async (token: string, id: string): Promise<boolean> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/scheduled-jobs/${id}`, {
		method: 'DELETE',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res?.ok ?? true;
};

export const runScheduledJob = async (token: string, id: string): Promise<void> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/scheduled-jobs/${id}/run`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err;
			console.error(err);
			return null;
		});

	if (error) throw error;
	return res;
};
