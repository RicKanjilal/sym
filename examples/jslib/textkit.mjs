// A tiny JS lib — imported into Sym via js.import
export function shout(s) { return s.toUpperCase() + "!!!"; }
export function slug(s)  { return s.toLowerCase().replace(/\s+/g, "-"); }
