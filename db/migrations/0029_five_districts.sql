-- Five areas on every plan, not seven.
--
-- The trial and the comp plan were already five; the two paid plans were seven,
-- so upgrading widened the filter as a side effect nobody asked for. One number
-- for everybody is easier to say on the page and easier to reason about.
UPDATE plans SET max_districts = 5 WHERE max_districts <> 5;
