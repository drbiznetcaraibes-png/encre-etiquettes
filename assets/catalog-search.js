// Shared server-backed search for tablet labels and console posters.
function bindCatalogSearch(root, category, onSelect) {
 const query = root.querySelector('input');
 const button = root.querySelector('button');
 const results = root.querySelector('select');
 const message = root.querySelector('[role="status"]');
 let request, products = [];

 function clearResults() {
  request?.abort();
  request = null;
  button.disabled = false;
  results.replaceChildren();
  results.parentElement.hidden = true;
  message.textContent = '';
  products = [];
  onSelect(null);
 }

 async function search() {
  clearResults();
  const pending = new AbortController();
  request = pending;
  button.disabled = true;
  message.textContent = 'Recherche dans le catalogue…';
  const params = new URLSearchParams({category, query: query.value.trim()});
  try {
   const response = await fetch('/api/products?' + params, {signal: pending.signal});
   const data = await response.json();
   if (request !== pending) return;
   if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Catalogue indisponible.');
   products = data.products;
   if (!products.length) {
    message.textContent = 'Aucun produit trouvé.';
    return;
   }
   results.add(new Option('Choisir une référence', ''));
   products.forEach((product, i) => {
    const name = catalogProductName(product);
    const details = [product.variant, product.reference, product.available_capacities.join(' / ')].filter(Boolean);
    results.add(new Option([name, ...details].join(' · '), String(i)));
   });
   results.parentElement.hidden = false;
   message.textContent = products.length === 1 ? '' : 'Choisis la référence exacte.';
   if (products.length === 1) {
    results.value = '0';
    onSelect(products[0]);
   }
  } catch (error) {
   if (request === pending) message.textContent = error.message || 'Catalogue indisponible.';
  } finally {
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
 results.addEventListener('change', () => onSelect(products[results.value] ?? null));

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
