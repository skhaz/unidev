const form = document.querySelector("#search-form");
const input = document.querySelector("#search-input");
const status = document.querySelector("#search-status");
const results = document.querySelector("#search-results");
const typeFilter = document.querySelector("#type-filter");
const forumFilter = document.querySelector("#forum-filter");
const yearFilter = document.querySelector("#year-filter");

let pagefind;

async function engine() {
	if (!pagefind) {
		const moduleUrl = new URL("../pagefind/pagefind.js", import.meta.url);
		pagefind = await import(moduleUrl);
		await pagefind.options({ baseUrl: "/unidev/" });
		await pagefind.init();
	}
	return pagefind;
}

function element(name, className, text) {
	const node = document.createElement(name);
	if (className) node.className = className;
	if (text) node.textContent = text;
	return node;
}

function renderItem(data) {
	const item = element("li", "result");
	const title = element("h3", "result-title");
	const link = element("a", "", data.meta.title || "Resultado sem título");
	link.href = data.url;
	title.append(link);

	const meta = element("p", "result-meta");
	const details = [
		data.meta.tipo,
		data.meta.autor,
		data.meta.forum,
		data.meta.data,
	]
		.filter(Boolean)
		.join(" · ");
	meta.textContent = details;

	const excerpt = element("p", "result-excerpt", data.excerpt || "");
	item.append(title, meta, excerpt);
	return item;
}

async function search() {
	const query = input.value.trim();
	const url = new URL(window.location.href);
	if (!query) {
		results.replaceChildren();
		status.textContent =
			"Digite um termo, trecho de código ou nome de usuário.";
		url.searchParams.delete("q");
		history.replaceState(null, "", url);
		return;
	}

	status.textContent = "Buscando…";
	const filters = {};
	if (typeFilter.value) filters.tipo = typeFilter.value;
	if (forumFilter.value && typeFilter.value !== "usuario")
		filters.forum = forumFilter.value;
	if (yearFilter.value && typeFilter.value !== "usuario")
		filters.ano = yearFilter.value;

	try {
		const searchEngine = await engine();
		const response = await searchEngine.search(query, { filters });
		const entries = await Promise.all(
			response.results.slice(0, 50).map((result) => result.data()),
		);
		results.replaceChildren(...entries.map(renderItem));
		status.textContent = `${response.results.length.toLocaleString("pt-BR")} resultado(s).`;
		url.searchParams.set("q", query);
		history.replaceState(null, "", url);
	} catch (error) {
		console.error(error);
		results.replaceChildren();
		status.textContent =
			"A busca ainda não foi gerada ou não pôde ser carregada.";
	}
}

form?.addEventListener("submit", (event) => {
	event.preventDefault();
	void search();
});

for (const filter of [typeFilter, forumFilter, yearFilter]) {
	filter?.addEventListener("change", () => void search());
}

const initial = new URL(window.location.href).searchParams.get("q");
if (initial && input) {
	input.value = initial;
	void search();
} else if (status) {
	status.textContent = "Digite um termo, trecho de código ou nome de usuário.";
}
