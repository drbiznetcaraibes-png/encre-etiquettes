// Shared Supabase-first search for tablet labels and console/TV posters.
function bindCatalogSearch(root, category, onSelect) {
 const query = root.querySelector('input');
 const button = root.querySelector('button');
 const results = root.querySelector('select');
 const message = root.querySelector('[role="status"]');
 let request, products = [], choices = [];

 function clearResults() {
  request?.abort();
  request = null;
  button.disabled = false;
  results.replaceChildren();
  results.parentElement.hidden = true;
  message.textContent = '';
  products = [];
  choices = [];
  onSelect(null);
 }

 async function search() {
  clearResults();
  const pending = new AbortController();
  request = pending;
  button.disabled = true;
  message.textContent = 'Recherche dans le catalogue ou sur le Web…';
  const timer = setTimeout(() => pending.abort(), 65000);
  try {
   const response = await fetch('/api/products/lookup', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, signal: pending.signal,
    body: JSON.stringify({category: typeof category === 'function' ? category() : category, query: query.value.trim()}),
   });
   const data = await response.json();
   if (request !== pending) return;
   if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Catalogue indisponible.');
   products = data.products;
   choices = data.choices || [];
   if (choices.length) {
    results.add(new Option('Choisir une variante', ''));
    choices.forEach((description, i) => results.add(new Option(description, String(i))));
    results.parentElement.hidden = false;
    message.textContent = 'Plusieurs variantes sont possibles. Choisis celle à rechercher.';
    return;
   }
   if (!products.length) {
    message.textContent = 'Aucun produit trouvé.';
    return;
   }
   results.add(new Option('Choisir une référence', ''));
   products.forEach((product, i) => {
    const name = catalogProductName(product);
    const details = [product.variant, product.reference, product.inches && product.inches + '″',
      product.capacity || product.available_capacities.join(' / ')].filter(Boolean);
    results.add(new Option([name, ...details].join(' · '), String(i)));
   });
   results.parentElement.hidden = false;
   const automatic = products.length === 1 && !data.choose_variant;
   message.textContent = automatic ? '' : 'Choisis la référence exacte.';
   if (automatic) {
    results.value = '0';
    onSelect(products[0]);
   }
  } catch (error) {
   if (request === pending) message.textContent = error.name === 'AbortError'
    ? 'La recherche a dépassé le délai prévu.' : error.message || 'Catalogue indisponible.';
  } finally {
   clearTimeout(timer);
   if (request === pending) {
    request = null;
    button.disabled = false;
   }
  }
 }

 query.addEventListener('input', clearResults);
 query.addEventListener('keydown', event => {
  if (event.key === 'Enter') {
   event.preventDefault();
   search();
  }
 });
 button.addEventListener('click', search);
 results.addEventListener('change', () => {
  if (choices[results.value]) {
   query.value = choices[results.value];
   search();
  } else {
   onSelect(products[results.value] ?? null);
  }
 });

 return {
  reset() {
   query.value = '';
   clearResults();
  }
 };
}

function catalogProductName(product) {
 const model = product.model;
 return model.toLowerCase().startsWith(product.brand.toLowerCase() + ' ') ? model : product.brand + ' ' + model;
}
