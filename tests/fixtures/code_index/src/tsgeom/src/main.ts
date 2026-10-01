// Command line entry point.

import { makeDisk } from "./shapes";
import { describe } from "./report";

console.log(describe(1, 2));
console.log(makeDisk(3).surface());
